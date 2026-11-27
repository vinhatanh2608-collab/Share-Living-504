import asyncio
from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import test_api  # Initializes local test settings.
from app.core.config import Settings, settings
from app.core.mongo_filters import lookup_id, sanitize_filter_values
from app.main import create_app
from app.schemas.auth import Login
from app.services.storage_service import PROOF_IMAGE_TYPES, finalize


def test_scalar_lookups_and_typed_sql_parameters():
    from sqlalchemy import select
    from sqlalchemy.dialects.mysql import dialect
    from app.models.mysql_models import User

    name = "O'Brien"
    statement = select(User).where(User.username == Login(username=name, password="test").username)
    compiled = statement.compile(dialect=dialect())
    assert name not in str(compiled) and name in compiled.params.values()
    assert lookup_id("literal.$text") == "literal.$text"


@pytest.mark.parametrize("key", ["$ne", "$gt", "nested.field"])
def test_document_operators_cannot_enter_user_filters(key):
    value = {key: "sample"}
    with pytest.raises(ValueError):
        sanitize_filter_values({"values": [value]})
    client = TestClient(create_app())
    response = client.post("/api/v1/auth/login", json={"username": value, "password": "test"})
    assert response.status_code == 422


@pytest.mark.parametrize(("declared_type", "payload", "status"), [
    ("image/svg+xml", b"<svg/>", 400),
    ("image/jpeg", b"plain text", 400),
    ("image/jpeg", b"x" * 5_000_001, 413),
])
def test_receipts_rejected_before_permanent_upload(declared_type, payload, status):
    storage = Mock()
    storage.head_object.return_value = {"ContentType": declared_type, "ContentLength": len(payload), "ETag": "same"}
    storage.get_object.return_value = {"Body": BytesIO(payload)}
    with patch("app.services.storage_service.client", return_value=storage), pytest.raises(HTTPException) as error:
        asyncio.run(finalize(1, "group-receipts/2", uuid4(), PROOF_IMAGE_TYPES, 5_000_000))
    assert error.value.status_code == status
    storage.put_object.assert_not_called()
    storage.copy_object.assert_not_called()
    if status == 413:
        storage.get_object.assert_not_called()


def test_stream_receipt_checks_actual_byte_count():
    storage = Mock()
    storage.head_object.return_value = {"ContentType": "image/jpeg", "ContentLength": 5, "ETag": "same"}
    body = BytesIO(b"x" * 5_000_001)
    storage.get_object.return_value = {"Body": body}
    with patch("app.services.storage_service.client", return_value=storage), pytest.raises(HTTPException) as error:
        asyncio.run(finalize(1, "group-receipts/2", uuid4(), PROOF_IMAGE_TYPES, 5_000_000))
    assert error.value.status_code == 413 and body.closed
    storage.put_object.assert_not_called()


def test_body_limits_cover_content_length_and_chunked_requests():
    client = TestClient(create_app())
    response = client.post("/api/v1/ai-chat/message", content=b"x" * 1_000_001,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    response = client.post("/api/v1/groups/1/proofs/upload", content=iter([b"x" * 1_000_000] * 6),
                           headers={"Content-Type": "image/jpeg"})
    assert response.status_code == 413


@pytest.mark.parametrize(("path", "limit", "method"), [
    ("/api/v1/auth/login", 5, "POST"),
    ("/api/v1/auth/register", 5, "POST"),
    ("/api/v1/ai-chat/message", 10, "POST"),
    ("/api/v1/groups/2/proofs/upload", 10, "POST"),
    ("/api/v1/no-such-route", 120, "GET"),
])
def test_rate_limits_return_retry_after(path, limit, method):
    client = TestClient(create_app())
    for _ in range(limit):
        assert client.request(method, path).status_code != 429
    response = client.request(method, path)
    assert response.status_code == 429 and 1 <= int(response.headers["Retry-After"]) <= 60


def test_ai_hourly_rate_limit(monkeypatch):
    config = settings().model_copy(update={"ai_requests_per_minute": 5, "ai_requests_per_hour": 6})
    now = 1000
    monkeypatch.setattr("app.core.request_security.monotonic", lambda: now)
    client = TestClient(create_app(config=config))
    for _ in range(5):
        assert client.post("/api/v1/ai-chat/message").status_code == 401
    now += 61
    assert client.post("/api/v1/ai-chat/message").status_code == 401
    response = client.post("/api/v1/ai-chat/message")
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 60


def test_production_security_headers_and_untrusted_proxy_header():
    values = settings().model_dump()
    values.update(environment="production", s3_endpoint="https://storage.example.net",
                  s3_public_endpoint=None, cors_origins=["https://app.example.net"])
    with pytest.raises(ValidationError):
        Settings.model_validate({**values, "cors_origins": ["http://app.example.net"]})
    app = create_app(config=Settings.model_validate(values))
    http = TestClient(app)
    https = TestClient(app, base_url="https://api.example.net")
    assert http.get("/health/live", headers={"X-Forwarded-Proto": "https"}).status_code == 400
    response = https.get("/health/live")
    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains; preload"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    preflight = {"Origin": "https://app.example.net", "Access-Control-Request-Method": "GET"}
    assert http.options("/health/live", headers=preflight).status_code == 400
    assert https.options("/health/live", headers=preflight).headers["X-Frame-Options"] == "DENY"
    proxy = TestClient(ProxyHeadersMiddleware(app, trusted_hosts=["testclient"]))
    assert proxy.get("/health/live", headers={"X-Forwarded-Proto": "https"}).status_code == 200
