from typing import Annotated
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..core.config import settings
from ..core.security import claims
from ..models.mysql_models import User

bearer = HTTPBearer(auto_error=False)


async def authenticate(resources, token):
    if not token or len(token) > 4096:
        return None
    try:
        payload = claims(token)
        uid = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        return None
    async with resources.sql() as sql:
        user = await sql.get(User, uid)
        return (user, payload["exp"]) if user and user.active else None


async def current_user(request: Request, auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    result = await authenticate(request.app.state.resources, auth.credentials) if auth else None
    if not result:
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    return result[0]


Actor = Annotated[User, Depends(current_user)]


def is_owner(user):
    return user.username == settings().owner_username
