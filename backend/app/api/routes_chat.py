from fastapi import APIRouter, HTTPException, Query, Request
from bson import ObjectId
from ..core.mongo_filters import lookup_id
from ..models.mysql_models import now
from ..schemas.chat import ChatMessage
from .deps import Actor

router = APIRouter(prefix="/chat", tags=["chat"])


def public(doc):
    return {"id": str(doc["_id"]), **{k: v for k, v in doc.items() if k not in {"_id", "audience"}}}


@router.get("/messages")
async def history(request: Request, actor: Actor, before: str | None = Query(None, max_length=64),
                  limit: int = Query(50, ge=1, le=100)):
    messages = request.app.state.resources.mongo.messages
    query = {"$or": [{"kind": "text"}, {"audience": actor.id}]}
    if before:
        before = lookup_id(before)
        key = ObjectId(before) if ObjectId.is_valid(before) else before
        cursor = await messages.find_one({"_id": key, **query}, {"_id": 0, "sort_key": 1})
        if not cursor:
            raise HTTPException(400, "Invalid cursor")
        # Homogeneous sort key avoids MongoDB's type bracketing across ObjectId and strings.
        query["sort_key"] = {"$lt": cursor["sort_key"]}
    docs = await messages.find(query).sort("sort_key", -1).limit(limit + 1).to_list(limit + 1)
    return {"items": [public(d) for d in docs[:limit]],
            "next_cursor": str(docs[limit - 1]["_id"]) if len(docs) > limit else None}


@router.post("/messages", status_code=201)
async def send(data: ChatMessage, request: Request, actor: Actor):
    if not data.text.strip():
        raise HTTPException(422, "Empty message")
    oid = ObjectId()
    doc = {"_id": oid, "sort_key": str(oid), "kind": "text", "sender_id": actor.id,
           "text": data.text, "created_at": now()}
    await request.app.state.resources.mongo.messages.insert_one(doc)
    result = public(doc)
    request.app.state.ws.publish({"type": "chat_message", "message": {
        **result, "created_at": result["created_at"].isoformat() + "Z"}})
    return result
