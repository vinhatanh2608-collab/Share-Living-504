import asyncio
import hashlib
import logging
from bson import ObjectId
from datetime import timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from ..models.mysql_models import User, Expense, Debt, Proof, Outbox, now
from .domain import shares, decision

log = logging.getLogger(__name__)


def expense_result(expense):
    return {"id": expense.id, "amount": expense.amount, "title": expense.title,
            "due_at": expense.due_at.replace(tzinfo=timezone.utc)}


async def create_expense(resources, actor, data):
    try:
        split = shares(data.amount, data.participants)
        if data.custom_shares is not None:
            if set(data.custom_shares) != set(split) or sum(data.custom_shares.values()) != data.amount:
                raise ValueError("Custom shares must match participants and total")
            split = data.custom_shares
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    request_id = str(data.request_id)
    digest = hashlib.sha256(data.model_dump_json(exclude_none=True).encode()).hexdigest()
    try:
        async with resources.sql.begin() as sql:
            ids = set(await sql.scalars(select(User.id).where(User.id.in_(split), User.active.is_(True))))
            if ids != set(split):
                raise HTTPException(422, "Unknown roommate")
            expense = Expense(creditor_id=actor, request_id=request_id, request_hash=digest,
                              title=data.title, amount=data.amount,
                              due_at=data.due_at.astimezone(timezone.utc).replace(tzinfo=None))
            sql.add(expense)
            await sql.flush()
            sql.add_all(Debt(expense_id=expense.id, debtor_id=uid, amount=value)
                        for uid, value in split.items() if uid != actor)
            return expense_result(expense)
    except IntegrityError:
        async with resources.sql() as sql:
            row = await sql.scalar(select(Expense).where(
                Expense.creditor_id == actor, Expense.request_id == request_id))
            if row is None:
                raise
            if row.request_hash != digest:
                raise HTTPException(409, "Request ID already used for another expense")
            return expense_result(row)


def proof_event(sql, proof, debt, creditor_id):
    sql.add(Outbox(payload={"proof_id": proof.id, "debt_id": debt.id, "status": proof.status,
                            "debtor_id": debt.debtor_id, "creditor_id": creditor_id}))


async def review(resources, actor, proof_id, approve):
    async with resources.sql.begin() as sql:
        debt_id = await sql.scalar(select(Proof.debt_id).where(Proof.id == proof_id))
        if debt_id is None:
            raise HTTPException(404, "Proof not found")
        debt = await sql.scalar(select(Debt).where(Debt.id == debt_id).with_for_update())
        proof = await sql.scalar(select(Proof).where(Proof.id == proof_id).with_for_update())
        expense = await sql.get(Expense, debt.expense_id)
        if expense.creditor_id != actor:
            raise HTTPException(403, "Only the creditor can verify this receipt")
        try:
            target = decision(True, proof.status, approve)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if target != proof.status:
            if debt.status != "pending":
                raise HTTPException(409, "Debt no longer pending")
            proof.status = target
            proof.decided_at = now()
            proof.decided_by = actor
            debt.status = "paid" if approve else "open"
            proof_event(sql, proof, debt, expense.creditor_id)
        return {"id": proof.id, "status": proof.status}


async def deliver_forever(resources, manager, owner_name):
    while True:
        published = None
        try:
            async with resources.sql.begin() as sql:
                row = await sql.scalar(select(Outbox).where(
                    Outbox.delivered.is_(False), Outbox.next_attempt <= now()
                ).order_by(Outbox.id).limit(1).with_for_update(skip_locked=True))
                if row:
                    data = row.payload
                    owner = await sql.scalar(select(User.id).where(User.username == owner_name))
                    audience = list({owner, data["creditor_id"], data["debtor_id"]} - {None})
                    try:
                        await resources.mongo.messages.update_one(
                            {"_id": f"proof-event:{row.id}"},
                            {"$setOnInsert": {"kind": "payment_card", "card": data,
                                              "audience": audience, "created_at": now(),
                                              "sort_key": str(ObjectId()),
                                              "event_id": row.id}}, upsert=True)
                    except Exception:
                        row.attempts += 1
                        row.next_attempt = now() + timedelta(seconds=min(300, 2 ** min(9, row.attempts)))
                        log.exception("Payment projection failed; queued for retry")
                    else:
                        row.delivered = True
                        published = ({"type": "payment_card", "event_id": row.id, "card": data}, audience)
            if published:
                manager.publish(*published)
            else:
                await asyncio.sleep(1)
        except Exception:
            log.exception("Outbox polling failed")
            await asyncio.sleep(3)
