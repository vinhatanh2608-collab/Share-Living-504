import asyncio
from datetime import date, datetime, timedelta, timezone
from secrets import token_hex

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from ..models.archive import PersonalHistory, personal_archive
from sqlalchemy.exc import IntegrityError

from .deps import Actor
from ..core.security import passwords
from ..models.living import Budget, GroupMember, LivingGroup, PersonalTransaction
from ..models.mysql_models import User
from ..services.cache_service import cached
from ..schemas.living import BankInfo, BudgetIn, GroupIn, JoinIn, MemberDecision, Register, TransactionIn

router = APIRouter(tags=["living"])


def today_local():
    return datetime.now(timezone(timedelta(hours=7))).date()


async def approved_member(sql, group_id: int, user_id: int, *, lock=False):
    row = await sql.get(GroupMember, (group_id, user_id), with_for_update=lock)
    if not row or row.status != "APPROVED":
        raise HTTPException(404, "Group unavailable")
    return row


def qr(member):
    if not member.bank_bin:
        return None
    return {key: getattr(member, key) for key in BankInfo.model_fields}


def group_view(group, member):
    return {"id": group.id, "name": group.name, "code": group.code if member.status == "APPROVED" else None,
            "role": "OWNER" if group.owner_id == member.user_id else "MEMBER", "status": member.status,
            "qr_ready": bool(member.bank_bin), "auto_monthly_reminder": group.auto_monthly_reminder,
            "reminder_hour": group.reminder_hour, "last_reminded_period": group.last_reminded_period}


@router.post("/auth/register", status_code=201)
async def register(data: Register, request: Request):
    async with request.app.state.login_slots:
        encoded = await asyncio.to_thread(passwords.hash, data.password)
    try:
        async with request.app.state.resources.sql.begin() as sql:
            user = User(username=data.username.lower(), name=data.full_name, password_hash=encoded)
            sql.add(user)
            await sql.flush()
            result = {"id": user.id, "username": user.username, "name": user.name}
    except IntegrityError as exc:
        duplicate = 1062 in getattr(exc.orig, "args", ()) or (
            getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE")
        if not duplicate:
            raise
        raise HTTPException(409, "Registration could not be completed") from exc
    return result


@router.get("/groups")
async def groups(request: Request, actor: Actor, limit: int = Query(500, ge=1, le=500),
                 after: int = Query(0, ge=0)):
    async with request.app.state.resources.sql() as sql:
        rows = await sql.execute(select(LivingGroup, GroupMember).join(GroupMember)
                                 .where(GroupMember.user_id == actor.id, LivingGroup.id > after).order_by(LivingGroup.id).limit(limit))
        return [group_view(group, member) for group, member in rows]


@router.post("/groups", status_code=201)
async def create_group(data: GroupIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        group = LivingGroup(code=f"GRP-{token_hex(6).upper()}", name=data.name, owner_id=actor.id)
        sql.add(group)
        await sql.flush()
        member = GroupMember(group_id=group.id, user_id=actor.id, status="APPROVED")
        sql.add(member)
        return group_view(group, member)


@router.post("/groups/join")
async def join(data: JoinIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        group = await sql.scalar(select(LivingGroup).where(LivingGroup.code == data.group_code).with_for_update())
        if not group:
            raise HTTPException(404, "Group code not found")
        member = await sql.get(GroupMember, (group.id, actor.id))
        if member:
            return group_view(group, member)
        member = GroupMember(group_id=group.id, user_id=actor.id, status="PENDING")
        sql.add(member)
    request.app.state.ws.publish({"type": "group_join_requested", "group_id": group.id}, [group.owner_id])
    return group_view(group, member)


@router.get("/groups/{group_id}/members")
async def members(group_id: int, request: Request, actor: Actor, limit: int = Query(500, ge=1, le=500),
                  offset: int = Query(0, ge=0, le=100000)):
    resources = request.app.state.resources
    async with resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        group = await sql.get(LivingGroup, group_id)
        owner = group.owner_id == actor.id

    async def load():
        async with resources.sql() as sql:
            query = select(GroupMember, User).join(User).where(GroupMember.group_id == group_id, User.active.is_(True))
            if not owner:
                query = query.where(GroupMember.status == "APPROVED")
            rows = await sql.execute(query.order_by(User.name, User.id).offset(offset).limit(limit))
            return [{"id": user.id, "name": user.name, "status": member.status,
                     "qr": qr(member) if member.status == "APPROVED" else None} for member, user in rows]

    return await cached(request, ["members", group_id, owner, limit, offset], load)


@router.patch("/groups/{group_id}/members/{user_id}")
async def decide_member(group_id: int, user_id: int, data: MemberDecision, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        group = await sql.get(LivingGroup, group_id, with_for_update=True)
        await approved_member(sql, group_id, actor.id, lock=True)
        if group.owner_id != actor.id:
            raise HTTPException(403, "Group owner approval required")
        member = await sql.get(GroupMember, (group_id, user_id), with_for_update=True)
        if not member:
            raise HTTPException(404, "Request not found")
        if member.status not in {"PENDING", data.status}:
            raise HTTPException(409, "Request already decided")
        member.status = data.status
    request.app.state.ws.publish({"type": "group_membership_updated", "group_id": group_id}, [user_id, actor.id])
    return {"status": member.status}


@router.put("/users/me/bank-info")
async def set_bank(data: BankInfo, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        user = await sql.get(User, actor.id)
        user.bank_bin = data.bank_bin
        user.account_number = data.bank_account_no
        user.account_name = data.bank_account_name
    return data


@router.get("/users/me/bank-info")
async def get_bank(request: Request, actor: Actor):
    async def load():
        return BankInfo(bank_bin=actor.bank_bin, bank_account_no=actor.account_number,
                        bank_account_name=actor.account_name) if actor.bank_bin else None

    return await cached(request, ["bank", actor.id, actor.bank_bin, actor.account_number, actor.account_name], load)


@router.put("/groups/{group_id}/members/me/qr")
async def set_group_bank(group_id: int, data: BankInfo, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        member = await approved_member(sql, group_id, actor.id, lock=True)
        for key, value in data.model_dump().items():
            setattr(member, key, value)
    return data


def transaction_view(entry):
    return {"id": entry.id, **{key: getattr(entry, key) for key in TransactionIn.model_fields}}


@router.get("/personal-expenses")
async def transactions(request: Request, actor: Actor, before: int | None = None):
    async with request.app.state.resources.sql() as sql:
        query = select(PersonalHistory).where(PersonalHistory.user_id == actor.id)
        if before is not None:
            query = query.where(PersonalHistory.id < before)
        rows = await sql.scalars(query.order_by(PersonalHistory.id.desc()).limit(100))
        return [transaction_view(row) for row in rows]


@router.post("/personal-expenses", status_code=201)
async def add_transaction(data: TransactionIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        entry = PersonalTransaction(user_id=actor.id, **data.model_dump())
        sql.add(entry)
        await sql.flush()
        return transaction_view(entry)


@router.put("/personal-expenses/{entry_id}")
async def replace_transaction(entry_id: int, data: TransactionIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        entry = await sql.get(PersonalTransaction, entry_id, with_for_update=True)
        if entry is None:
            query = personal_archive.update().where(personal_archive.c.id == entry_id,
                personal_archive.c.user_id == actor.id).values(**data.model_dump())
            existing = await sql.scalar(select(personal_archive.c.id).where(
                personal_archive.c.id == entry_id, personal_archive.c.user_id == actor.id).with_for_update())
            if existing is None:
                raise HTTPException(404, "Transaction not found")
            await sql.execute(query)
            return {"id": entry_id, **data.model_dump()}
        if entry.user_id != actor.id:
            raise HTTPException(404, "Transaction not found")
        for key, value in data.model_dump().items():
            setattr(entry, key, value)
        return transaction_view(entry)


@router.delete("/personal-expenses/{entry_id}", status_code=204)
async def remove_transaction(entry_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        entry = await sql.get(PersonalTransaction, entry_id, with_for_update=True)
        if entry is None:
            deleted = await sql.execute(personal_archive.delete().where(personal_archive.c.id == entry_id,
                personal_archive.c.user_id == actor.id))
            if not deleted.rowcount:
                raise HTTPException(404, "Transaction not found")
            return
        if entry.user_id != actor.id:
            raise HTTPException(404, "Transaction not found")
        await sql.delete(entry)


@router.get("/budgets")
async def budgets(request: Request, actor: Actor, month: date | None = None):
    target = (month or today_local()).replace(day=1)
    async with request.app.state.resources.sql() as sql:
        rows = await sql.scalars(select(Budget).where(Budget.user_id == actor.id, Budget.month == target)
                                 .order_by(Budget.category))
        return [{key: getattr(row, key) for key in BudgetIn.model_fields} for row in rows]


@router.put("/budgets")
async def save_budget(data: BudgetIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await sql.get(User, actor.id, with_for_update=True)
        row = await sql.get(Budget, (actor.id, data.month, data.category))
        if row:
            row.monthly_limit = data.monthly_limit
        else:
            sql.add(Budget(user_id=actor.id, **data.model_dump()))
    return data
