import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from ..core.security import issue, passwords
from ..models.mysql_models import User
from ..schemas.auth import Login, Token, Profile, VietQR
from ..services.cache_service import cached
from .deps import Actor, is_owner

router = APIRouter(prefix="/auth", tags=["auth"])
legacy_router = APIRouter(prefix="/auth", tags=["legacy room"])


@router.post("/login", response_model=Token)
async def login(data: Login, request: Request):
    username = data.username if request.app.state.legacy_room else data.username.lower()
    async with request.app.state.resources.sql() as sql:
        user = await sql.scalar(select(User).where(User.username == username))
    candidate = user.password_hash if user else request.app.state.dummy_password
    async with request.app.state.login_slots:
        valid = await asyncio.to_thread(passwords.verify, data.password, candidate)
    if not user or not user.active or not valid:
        raise HTTPException(401, "Invalid credentials")
    return Token(access_token=issue(user.id))


@router.get("/me")
async def me(request: Request, actor: Actor):
    owner = request.app.state.legacy_room and is_owner(actor)

    async def load():
        return {**Profile.model_validate(actor).model_dump(), "is_owner": owner}

    return await cached(request, ["profile", actor.id, actor.name, actor.username, owner], load)


@legacy_router.put("/me/vietqr")
async def update_bank(data: VietQR, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        user = await sql.get(User, actor.id)
        for key, value in data.model_dump().items():
            setattr(user, key, value)
    return data


@legacy_router.get("/roommates")
async def roommates(request: Request, actor: Actor, limit: int = Query(500, ge=1, le=500),
                    after: int = Query(0, ge=0)):
    async with request.app.state.resources.sql() as sql:
        users = await sql.scalars(select(User).where(User.active.is_(True), User.id > after).order_by(User.id).limit(limit))
        return [{**Profile.model_validate(user).model_dump(),
                 "vietqr": (VietQR.model_validate(user, from_attributes=True)
                            if user.bank_bin else None)} for user in users]
