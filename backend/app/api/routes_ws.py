import asyncio
import logging
import time
from anyio import CancelScope
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pymongo.errors import PyMongoError
from sqlalchemy.exc import SQLAlchemyError
from ..models.living import GroupMember
from .deps import authenticate

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket(socket: WebSocket):
    origin = socket.headers.get("origin")
    if origin and origin not in socket.app.state.cors_origins:
        await socket.close(code=1008)
        return
    protocols = socket.scope.get("subprotocols", [])
    selected = "bearer" if len(protocols) == 2 and protocols[0] == "bearer" else None
    header = socket.headers.get("authorization", "")
    token = (protocols[1] if selected else
             header[7:] if header.lower().startswith("bearer ") else socket.query_params.get("token", ""))
    resources = socket.app.state.resources
    group_id = None
    reason = "unauthorized"
    try:
        auth = await authenticate(resources, token)
        requested_group = socket.query_params.get("group_id")
        if auth and requested_group is not None:
            reason = "group_membership_required"
            auth_user = auth[0]
            if not (requested_group.isascii() and requested_group.isdecimal() and 0 < len(requested_group) <= 10):
                auth = None
            else:
                group_id = int(requested_group)
                if 0 < group_id <= 2_147_483_647:
                    async with resources.sql() as sql:
                        member = await sql.get(GroupMember, (group_id, auth_user.id))
                        if not member or member.status != "APPROVED":
                            auth = None
                else:
                    auth = None
    except (SQLAlchemyError, PyMongoError):
        logger.exception("WebSocket authentication unavailable")
        await socket.close(code=1013)
        return
    if not auth:
        logger.warning("WebSocket rejected", extra={"reason": reason, "group_id": group_id})
        # Browsers hide HTTP upgrade denials behind close code 1006.
        await socket.accept(subprotocol=selected)
        await socket.close(code=1008)
        return
    actor, expiry = auth
    manager = socket.app.state.ws
    if len(manager.clients.get(actor.id, ())) >= 5:
        logger.warning("WebSocket rejected", extra={"reason": "account_connection_limit"})
        await socket.accept(subprotocol=selected)
        await socket.close(code=1008)
        return
    queue = manager.attach(actor.id)

    async def sender():
        await socket.send_json({"type": "connected"})
        loop = asyncio.get_running_loop()
        heartbeat_at = loop.time() + 15
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=max(0, heartbeat_at - loop.time()))
            except TimeoutError:
                event = {"type": "heartbeat"}
                heartbeat_at = loop.time() + 15
            event_group = event.get("group_id")
            if group_id is not None and event_group != group_id and event.get("type") not in {"heartbeat", "resync"}:
                continue
            # Account connections also carry an applicant's own join status.
            if event_group is not None:
                async with resources.sql() as sql:
                    member = await sql.get(GroupMember, (event_group, actor.id))
                    if not member or (member.status != "APPROVED" and
                                      (group_id is not None or event.get("type") != "group_membership_updated")):
                        continue
            await asyncio.wait_for(socket.send_json(event), timeout=5)

    async def receiver():
        from collections import deque
        frames = deque()
        while True:
            event = await socket.receive()
            if event["type"] == "websocket.receive":
                now = time.monotonic()
                while frames and frames[0] <= now - 10:
                    frames.popleft()
                if len(frames) >= 20:
                    await socket.close(code=1008)
                    return
                frames.append(now)
            if event["type"] == "websocket.disconnect":
                return

    tasks = []
    try:
        await socket.accept(subprotocol=selected)
        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        done, _ = await asyncio.wait(tasks, timeout=max(0, expiry - time.time()),
                                     return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if not done:
            await socket.close(code=1008)
        elif tasks[0] in done:
            await socket.close(code=1011)
    except WebSocketDisconnect:
        pass
    except (TimeoutError, SQLAlchemyError):
        logger.exception("WebSocket delivery failed", extra={"group_id": group_id})
        await socket.close(code=1011)
    finally:
        manager.detach(actor.id, queue)
        for task in tasks:
            task.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
