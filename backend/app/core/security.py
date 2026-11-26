from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext
from .config import settings

passwords = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)
ISSUER = "dormitory-api"


def issue(uid: int):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(uid), "iat": now, "exp": now + timedelta(minutes=30),
         "iss": ISSUER, "aud": "dorm-desktop", "type": "access"},
        settings().jwt_secret.get_secret_value(), algorithm="HS256",
    )


def claims(token):
    payload = jwt.decode(token, settings().jwt_secret.get_secret_value(),
                         algorithms=["HS256"], issuer=ISSUER, audience="dorm-desktop",
                         options={"require": ["sub", "iat", "exp", "iss", "aud", "type"]})
    subject = payload["sub"]
    if (payload["type"] != "access" or not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal()
            or len(subject) > 10 or not 0 < int(subject) <= 2**31 - 1
            or type(payload["exp"]) is not int or type(payload["iat"]) is not int
            or payload["exp"] <= payload["iat"]):
        raise jwt.InvalidTokenError("Invalid access token claims")
    return payload
