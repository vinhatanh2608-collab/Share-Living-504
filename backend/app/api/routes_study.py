from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from ..models.mysql_models import now
from ..schemas.study import MaterialCreate, MaterialData, MaterialUpload, Note
from ..services.storage_service import upload_ticket, finalize, download
from .deps import Actor, is_owner

router = APIRouter(prefix="/study", tags=["study"])
TYPES = {"application/pdf", "text/plain"}


def view(doc):
    return {"id": doc["_id"], **{k: v for k, v in doc.items() if k not in {"_id", "key"}}}


@router.post("/uploads")
async def ticket(data: MaterialUpload, actor: Actor):
    return await upload_ticket(actor.id, "study", data)


@router.post("/materials", status_code=201)
async def create(data: MaterialCreate, request: Request, actor: Actor):
    coll = request.app.state.resources.mongo.materials
    ident = f"{actor.id}:{data.upload_id}"
    old = await coll.find_one({"_id": ident})
    if old:
        return view(old)
    key = await finalize(actor.id, "study", data.upload_id, TYPES, 20_000_000)
    doc = {"_id": ident, **data.model_dump(exclude={"upload_id"}), "owner_id": actor.id,
           "key": key, "created_at": now()}
    try:
        await coll.insert_one(doc)
    except DuplicateKeyError:
        doc = await coll.find_one({"_id": ident})
        if not doc:
            raise HTTPException(409, "Material changed; refresh")
    return view(doc)


@router.get("/materials")
async def search(request: Request, actor: Actor, q: str = Query("", max_length=200),
                 after: str = Query("", max_length=64), limit: int = Query(50, ge=1, le=100)):
    query = {"_id": {"$gt": after}}
    if q.strip():
        query["$text"] = {"$search": q}
    docs = await request.app.state.resources.mongo.materials.find(query).sort("_id", 1).limit(limit + 1).to_list(limit + 1)
    return {"items": [view(doc) for doc in docs[:limit]],
            "next_cursor": docs[limit - 1]["_id"] if len(docs) > limit else None}


@router.get("/materials/{ident}")
async def get_material(ident: str, request: Request, actor: Actor):
    doc = await request.app.state.resources.mongo.materials.find_one({"_id": ident})
    if not doc:
        raise HTTPException(404, "Material not found")
    return {**view(doc), "url": await download(doc["key"], namespace="study", user_id=doc["owner_id"])}


def editable(ident, actor):
    return {"_id": ident, **({} if is_owner(actor) else {"owner_id": actor.id})}


@router.put("/materials/{ident}")
async def update(ident: str, data: MaterialData, request: Request, actor: Actor):
    coll = request.app.state.resources.mongo.materials
    doc = await coll.find_one_and_update(editable(ident, actor), {"$set": data.model_dump()},
                                         return_document=ReturnDocument.AFTER)
    if not doc:
        raise HTTPException(404, "Editable material not found")
    return view(doc)


@router.delete("/materials/{ident}", status_code=204)
async def delete(ident: str, request: Request, actor: Actor):
    deleted = await request.app.state.resources.mongo.materials.delete_one(editable(ident, actor))
    if not deleted.deleted_count:
        raise HTTPException(404, "Editable material not found")


@router.get("/notes/{name}")
async def get_note(name: str, request: Request, actor: Actor):
    if not 1 <= len(name) <= 100:
        raise HTTPException(422, "Invalid note name")
    doc = await request.app.state.resources.mongo.notes.find_one({"_id": name})
    return view(doc) if doc else {"id": name, "text": "", "version": 0}


@router.put("/notes/{name}")
async def put_note(name: str, data: Note, request: Request, actor: Actor):
    if not 1 <= len(name) <= 100:
        raise HTTPException(422, "Invalid note name")
    try:
        doc = await request.app.state.resources.mongo.notes.find_one_and_update(
            {"_id": name, "version": data.expected_version},
            {"$set": {"text": data.text, "editor_id": actor.id, "updated_at": now()},
             "$inc": {"version": 1}}, upsert=data.expected_version == 0,
            return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        doc = None
    if not doc:
        raise HTTPException(409, "Note changed; reload before editing")
    request.app.state.ws.publish({"type": "note_updated", "id": name, "version": doc["version"]})
    return view(doc)
