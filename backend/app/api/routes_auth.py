import asyncio
from collections import deque
from time import monotonic
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from ..core.security import issue, passwords
from ..models.mysql_models import User
from ..schemas.auth import Login, Token, Profile, VietQR
from .deps import Actor, is_owner

router = APIRouter(prefix="/auth", tags=["auth"])
legacy_router = APIRouter(prefix="/auth", tags=["legacy room"])


class AuthRateLimiter:
    def __init__(self):
        self.attempts = {}
        self.next_cleanup = 0.0

    def check(self, request, operation, limit):
        now = monotonic()
        window = 60
        if now >= self.next_cleanup:
            self.attempts = {key: hits for key, hits in self.attempts.items() if hits[-1] > now - window}
            self.next_cleanup = now + window
        host = request.client.host if request.client else "unknown"
        key = (operation, host)
        if key not in self.attempts and len(self.attempts) >= 10_000:
            raise HTTPException(429, "Too many attempts. Try again shortly.", headers={"Retry-After": "60"})
        hits = self.attempts.setdefault(key, deque())
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(429, "Too many attempts. Try again shortly.", headers={"Retry-After": "60"})
        hits.append(now)


@router.post("/login", response_model=Token)
async def login(data: Login, request: Request):
    request.app.state.auth_limiter.check(request, "login", 12)
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
    return {**Profile.model_validate(actor).model_dump(), "is_owner": request.app.state.legacy_room and is_owner(actor)}


@legacy_router.put("/me/vietqr")
async def update_bank(data: VietQR, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        user = await sql.get(User, actor.id)
        for key, value in data.model_dump().items():
            setattr(user, key, value)
    return data


@legacy_router.get("/roommates")
async def roommates(request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        users = await sql.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
        return [{**Profile.model_validate(user).model_dump(),
                 "vietqr": (VietQR.model_validate(user, from_attributes=True)
                            if user.bank_bin else None)} for user in users]
