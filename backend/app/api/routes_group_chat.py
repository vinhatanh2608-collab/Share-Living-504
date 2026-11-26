from datetime import timedelta, timezone
from secrets import token_hex

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo.errors import PyMongoError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
import asyncio
import logging

from .deps import Actor
from .routes_living import approved_member
from ..models.living import GroupEvent, GroupMember, GroupProof
from ..models.mysql_models import now
from ..schemas.living import ChatIn

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
        anchor = await resources.mongo.group_messages.find_one({"group_id": group_id, "_id": before},
                                                               {"_id": 0, "created_at": 1})
        if not anchor:
            raise HTTPException(404, "Chat cursor unavailable")
        selector["$or"] = [{"created_at": {"$lt": anchor["created_at"]}},
                           {"created_at": anchor["created_at"], "_id": {"$lt": before}}]
    rows = await resources.mongo.group_messages.find(selector).sort([("created_at", -1), ("_id", -1)]).limit(50).to_list(50)
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


async def project_forever(resources, manager):
    while True:
        try:
            if not await project_one(resources, manager):
                await asyncio.sleep(1)
        except SQLAlchemyError:
            logger.warning("Group outbox unavailable", exc_info=True)
            await asyncio.sleep(5)
