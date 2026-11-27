import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import test_api
from test_living import client, account
from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.core.security import issue
from app.main import create_app


@pytest.mark.parametrize(("path", "method"), [("/api/v1/auth/login", "POST"), ("/missing", "GET")])
def test_ip_budget_ignores_untrusted_headers(path, method):
    cfg = settings().model_copy(update={"trust_proxy": False, "api_requests_per_minute": 3,
                                        "auth_requests_per_minute": 2})
    http = TestClient(create_app(config=cfg))
    for index in range(4):
        response = http.request(method, path, headers={"X-Forwarded-For": f"192.0.2.{index}"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_state_is_bounded():
    limiter = RateLimiter(128)
    for index in range(3000):
        limiter.check(("general", index), 120, 60, index / 100)
    assert len(limiter.entries) == 128


@pytest.mark.parametrize(("mime", "size"), [("application/json", 1000001), ("multipart/form-data", 5065537)])
def test_declared_oversize(client, mime, size):
    response = client.post("/api/v1/groups/1/proofs/upload", headers={"Content-Type": mime, "Content-Length": str(size)})
    assert response.status_code == 413


def test_socket_frame_budget(client):
    _, headers = account(client, "socket-limit")
    with client.websocket_connect("/ws", headers=headers) as socket:
        assert socket.receive_json()["type"] == "connected"
        for _ in range(21):
            socket.send_text("ping")
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008


def test_multipart_overhead_does_not_trigger_body_limit(client):
    response = client.post("/api/v1/groups/1/proofs/upload",
                           files={"file": ("receipt.jpg", b"x" * 5_000_000, "image/jpeg")})
    assert response.status_code == 400
    assert response.json()["detail"] == "Use a signed upload URL for receipt bytes"


def test_authenticated_navigation_does_not_share_budget_with_other_accounts():
    cfg = settings().model_copy(update={"api_requests_per_minute": 2})
    http = TestClient(create_app(config=cfg))
    first = {"Authorization": f"Bearer {issue(1)}"}
    second = {"Authorization": f"Bearer {issue(2)}"}
    for headers in (first, second, first, second):
        assert http.get("/api/v1/no-such-route", headers=headers).status_code == 404
    assert http.get("/api/v1/no-such-route", headers=first).status_code == 429
