from datetime import timedelta, timezone
import json
from uuid import UUID, uuid4

from secrets import token_hex

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo.errors import PyMongoError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
import asyncio
import logging

from .deps import Actor
from .routes_group_finance import lock_group, save_expense
from .routes_living import approved_member
from ..core.mongo_filters import lookup_id
from ..models.living import GroupEvent, GroupExpenseDraft, GroupMember, GroupProof, GroupReminder
from ..models.mysql_models import User, now
from ..schemas.living import ChatIn, GroupExpenseIn, ParticipantIn
from ..services.advisor import provider_reply
from ..services.chat_expense import parse_expense
from ..services.monthly_reminders import project_monthly

router = APIRouter(prefix="/groups/{group_id}/chat", tags=["group-chat"])
logger = logging.getLogger(__name__)


async def audience(sql, group_id):
    return set(await sql.scalars(select(GroupMember.user_id).where(
        GroupMember.group_id == group_id, GroupMember.status == "APPROVED")))


def view(row):
    item = {**row, "id": row["_id"]}
    del item["_id"]
    item["created_at"] = item["created_at"].replace(tzinfo=timezone.utc).isoformat()
    return item


@router.get("")
async def chat(group_id: int, request: Request, actor: Actor, before: str | None = Query(None, max_length=80)):
    resources = request.app.state.resources
    selector = {"group_id": group_id}
    async with resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
    if before:
        before = lookup_id(before)
        anchor = await resources.mongo.group_messages.find_one({"group_id": group_id, "_id": before},
                                                               {"_id": 0, "created_at": 1})
        if not anchor:
            raise HTTPException(404, "Chat cursor unavailable")
        selector["$or"] = [{"created_at": {"$lt": anchor["created_at"]}},
                           {"created_at": anchor["created_at"], "_id": {"$lt": before}}]
    rows = await resources.mongo.group_messages.find(selector).sort([("created_at", -1), ("_id", -1)]).limit(50).to_list(50)
    draft_ids = [row["draft_id"] for row in rows if row.get("kind") == "expense_draft" and row.get("draft_id")]
    if draft_ids:
        async with resources.sql() as sql:
            drafts = await sql.scalars(select(GroupExpenseDraft).where(
                GroupExpenseDraft.group_id == group_id, GroupExpenseDraft.id.in_(draft_ids)))
            statuses = {draft.id: draft.status for draft in drafts}
        for row in rows:
            if row.get("kind") == "expense_draft" and row.get("draft_id"):
                row["status"] = statuses.get(row["draft_id"], "cancelled")
    return [view(row) for row in reversed(rows)]


@router.post("", status_code=201)
async def send(group_id: int, data: ChatIn, request: Request, actor: Actor):
    resources = request.app.state.resources
    async with resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        recipients = await audience(sql, group_id)
    row = {"_id": f"message:{token_hex(16)}", "group_id": group_id, "sender_id": actor.id,
           "sender_name": actor.name, "kind": "TEXT", "text": data.text, "created_at": now()}
    await resources.mongo.group_messages.insert_one(row)
    request.app.state.ws.publish({"type": "group_chat", "group_id": group_id, "message": view(row)}, recipients)
    return view(row)


async def project_one(resources, manager):
    async with resources.sql.begin() as sql:
        event = await sql.scalar(select(GroupEvent).where(GroupEvent.delivered.is_(False),
            GroupEvent.next_attempt <= now()).order_by(GroupEvent.next_attempt, GroupEvent.id).limit(1).with_for_update())
        if not event:
            return False
        proof = await sql.get(GroupProof, event.proof_id)
        row = {"_id": f"proof:{proof.id}", "group_id": proof.group_id, "sender_id": proof.debtor_id,
               "kind": "PAYMENT_PROOF", "created_at": proof.created_at, "proof_id": proof.id,
               "creditor_id": proof.creditor_id, "status": proof.status}
        try:
            await resources.mongo.group_messages.replace_one({"_id": row["_id"], "group_id": proof.group_id}, row, upsert=True)
        except PyMongoError:
            logger.warning("Group proof projection delayed", exc_info=True)
            event.next_attempt = now() + timedelta(seconds=10)
            return True
        recipients = await audience(sql, proof.group_id)
        event.delivered = True
        status = proof.status
        target = proof.creditor_id if status == "PENDING" else proof.debtor_id
    manager.publish({"type": "group_chat", "group_id": row["group_id"], "message": view(row)}, recipients)
    manager.publish({"type": "group_proof_submitted" if status == "PENDING" else "PROOF_VERIFIED",
                     "group_id": row["group_id"], "proof_id": row["proof_id"], "status": status}, [target])
    return True


async def project_reminder(resources, manager):
    async with resources.sql.begin() as sql:
        reminder = await sql.scalar(select(GroupReminder).where(GroupReminder.next_attempt <= now())
            .order_by(GroupReminder.next_attempt, GroupReminder.id).limit(1).with_for_update())
        if not reminder:
            return False
        row = {"_id": f"reminder:{reminder.id}", "group_id": reminder.group_id, "kind": "DEBT_REMINDER",
               "sender_id": reminder.sender_id, "sender_name": reminder.sender_name,
               "debtor_ids": reminder.debtor_ids, "created_at": reminder.created_at}
        try:
            await resources.mongo.group_messages.replace_one({"_id": row["_id"]}, row, upsert=True)
        except PyMongoError:
            logger.warning("Group reminder projection delayed", exc_info=True)
            reminder.next_attempt = now() + timedelta(seconds=10)
            return True
        recipients = await audience(sql, reminder.group_id)
        await sql.delete(reminder)
    manager.publish({"type": "group_chat", "group_id": row["group_id"], "message": view(row)}, recipients)
    return True


async def project_forever(resources, manager):
    while True:
        try:
            reminded = await project_reminder(resources, manager)
            monthly = await project_monthly(resources, manager)
            if not await project_one(resources, manager) and not reminded and not monthly:
                await asyncio.sleep(1)
        except SQLAlchemyError:
            logger.warning("Group outbox unavailable", exc_info=True)
            await asyncio.sleep(5)


@router.post("/parse-expense", status_code=201)
async def draft_expense(group_id: int, data: ChatIn, request: Request, actor: Actor):
    resources = request.app.state.resources
    async with resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        members = (await sql.execute(select(User.id, User.name, User.username).join(
            GroupMember, GroupMember.user_id == User.id).where(
            GroupMember.group_id == group_id, GroupMember.status == "APPROVED").order_by(User.id))).all()
    payload = parse_expense(data.text, members, actor.id)
    # Send only the parsed title, without the participant list or account identifiers.
    state = request.app.state
    if hasattr(state, "http") and hasattr(state, "advisor_breaker"):
        reply = await provider_reply("Trả JSON với trường title, viết gọn tên khoản chi tiếng Việt.",
            {"title": payload["title"]}, state.http, state.advisor_breaker, structured=True, gemini_only=True)
        try:
            title = json.loads(reply or "{}").get("title")
            if isinstance(title, str) and title.strip():
                payload["title"] = title.strip()[:200]
        except (ValueError, AttributeError):
            pass
    draft_id = str(uuid4())
    async with resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        sql.add(GroupExpenseDraft(id=draft_id, group_id=group_id, sender_id=actor.id, payload=payload))
        recipients = await audience(sql, group_id)
    row = {"_id": f"draft:{draft_id}", "draft_id": draft_id, "kind": "expense_draft",
           "group_id": group_id, "sender_id": actor.id, "sender_name": actor.name,
           "status": "pending", "preview": payload, "created_at": now()}
    await resources.mongo.group_messages.insert_one(row)
    message = view(row)
    state.ws.publish({"type": "group_chat", "group_id": group_id, "message": message}, recipients)
    return message


async def finish_draft(group_id, draft_id, request, actor, cancel=False):
    state = request.app.state
    async with state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        draft = await sql.scalar(select(GroupExpenseDraft).where(
            GroupExpenseDraft.group_id == group_id, GroupExpenseDraft.id == str(draft_id)).with_for_update())
        if not draft:
            raise HTTPException(404, "Không tìm thấy bản nháp.")
        if cancel and draft.sender_id != actor.id:
            raise HTTPException(403, "Chỉ người tạo được hủy bản nháp.")
        target = "cancelled" if cancel else "confirmed"
        if draft.status not in ("pending", target):
            raise HTTPException(409, "Bản nháp đã được xử lý.")
        if draft.status == "pending":
            if not cancel:
                participants = [ParticipantIn.model_validate(row) for row in draft.payload["participants"]]
                payer = next(row.user_id for row in participants if row.status == "paid")
                await approved_member(sql, group_id, payer, lock=True)
                data = GroupExpenseIn(request_id=draft_id, participants=participants,
                    **{key: draft.payload[key] for key in ("title", "category", "amount")})
                draft.expense_id = (await save_expense(sql, group_id, payer, data))["id"]
            draft.status = target
        result = {"status": target, "id": draft.expense_id}
        recipients = await audience(sql, group_id)
    state.ws.publish({"type": "group_chat", "group_id": group_id, "draft_id": str(draft_id),
                      "status": target}, recipients)
    return result


@router.post("/confirm-expense/{draft_id}")
async def confirm_expense(group_id: int, draft_id: UUID, request: Request, actor: Actor):
    return await finish_draft(group_id, draft_id, request, actor)


@router.post("/cancel-expense/{draft_id}")
async def cancel_expense(group_id: int, draft_id: UUID, request: Request, actor: Actor):
    return await finish_draft(group_id, draft_id, request, actor, cancel=True)
