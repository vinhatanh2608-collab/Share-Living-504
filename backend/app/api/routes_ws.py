import asyncio
import time
from anyio import CancelScope
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pymongo.errors import PyMongoError
from sqlalchemy.exc import SQLAlchemyError
from ..models.living import GroupMember
from ..core.config import settings
from .deps import authenticate

router = APIRouter()


@router.websocket("/ws")
async def websocket(socket: WebSocket):
    origin = socket.headers.get("origin")
    if origin and origin not in settings().cors_origins:
        await socket.close(code=1008)
        return
    header = socket.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else socket.query_params.get("token", "")
    try:
        auth = await authenticate(socket.app.state.resources, token)
    except (SQLAlchemyError, PyMongoError):
        await socket.close(code=1013)
        return
    if not auth:
        await socket.close(code=1008)
        return
    actor, expiry = auth
    manager = socket.app.state.ws
    if len(manager.clients.get(actor.id, ())) >= 5:
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
            group_id = event.get("group_id")
            # This targeted control event lets an applicant refresh their own join status.
            if group_id is not None:
                async with socket.app.state.resources.sql() as sql:
                    member = await sql.get(GroupMember, (group_id, actor.id))
                    if not member or (member.status != "APPROVED"
                                      and event.get("type") != "group_membership_updated"):
                        continue
            await asyncio.wait_for(socket.send_json(event), timeout=5)

    async def receiver():
        while True:
            event = await socket.receive()
            if event["type"] == "websocket.disconnect":
                return

    tasks = []
    try:
        await socket.accept()
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
        await socket.close(code=1011)
    finally:
        manager.detach(actor.id, queue)
        for task in tasks:
            task.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
