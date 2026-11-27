import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_api  # Initialize development settings from .env.example.
from app.api.routes_advisor import today_local
from app.api.routes_group_chat import project_one, project_reminder
from app.core.security import passwords
from app.main import create_app
from app.models.mysql_models import Base
from app.services.advisor import provider_reply
from app.core.circuit_breaker import CircuitBreaker
from app.services.ws_manager import Manager


class Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, row):
        self.rows.append({"_id": len(self.rows) + 1, **row})

    async def replace_one(self, selector, row, *, upsert):
        self.rows = [item for item in self.rows if item["_id"] != selector["_id"]] + [dict(row)]

    def find(self, selector, projection=None):
        def matches(item, conditions):
            for key, value in conditions.items():
                if key == "$or":
                    if not any(matches(item, clause) for clause in value):
                        return False
                elif isinstance(value, dict) and "$lt" in value:
                    if not item[key] < value["$lt"]:
                        return False
                elif item.get(key) != value:
                    return False
            return True

        return Cursor([item for item in self.rows if matches(item, selector)], projection)

    async def find_one(self, selector, projection=None):
        rows = await self.find(selector, projection).to_list(1)
        return rows[0] if rows else None


class Cursor:
    def __init__(self, rows, projection):
        self.rows = rows
        self.projection = projection or {}

    def sort(self, fields, direction=None):
        keys = [(fields, direction)] if isinstance(fields, str) else fields
        for key, order in reversed(keys):
            self.rows.sort(key=lambda item: item[key], reverse=order == -1)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, count):
        return [{key: value for key, value in item.items() if self.projection.get(key, 1)}
                for item in self.rows[:count]]


@pytest.fixture
def client():
    app = create_app(legacy_room=False)
    engine = create_async_engine("sqlite+aiosqlite://")

    @asynccontextmanager
    async def lifespan(_app):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        app.state.http = Mock()
        app.state.advisor_breaker = CircuitBreaker()
        app.state.resources = SimpleNamespace(sql=async_sessionmaker(engine, expire_on_commit=False),
            mongo=SimpleNamespace(group_messages=Collection(), ai_chat_history=Collection()))
        app.state.ws = Manager()
        app.state.login_slots = asyncio.Semaphore(4)
        app.state.dummy_password = passwords.hash("unused-password-hash")
        yield
        await engine.dispose()

    app.router.lifespan_context = lifespan
    with TestClient(app) as test:
        yield test


def account(client, username):
    password = "test-only-long-password"
    result = client.post("/api/v1/auth/register", json={"username": username, "password": password, "full_name": username})
    assert result.status_code == 201, result.text
    logged_in = client.post("/api/v1/auth/login", json={"username": username.upper(), "password": password})
    assert logged_in.status_code == 200, logged_in.text
    token = logged_in.json()["access_token"]
    return result.json()["id"], {"Authorization": f"Bearer {token}"}


def test_group_workflow_scopes_qr_chat_and_receipts_to_members(client):
    member_id, member = account(client, "debtor")
    owner_id, owner = account(client, "creditor")
    outsider_id, outsider = account(client, "outsider")
    root = "/api/v1/groups"
    group = client.post(root, headers=owner, json={"name": "Test household"}).json()
    assert group["role"] == "OWNER"
    path = f"{root}/{group['id']}"
    assert client.get(f"{path}/members", headers=outsider).status_code == 404
    other_group = client.post(root, headers=outsider, json={"name": "Other household"}).json()
    origin = {"Origin": "http://127.0.0.1:1420"}
    owner_token = owner["Authorization"].split()[1]
    with client.websocket_connect("/ws", headers=origin, subprotocols=["bearer", owner_token]) as socket:
        assert socket.accepted_subprotocol == "bearer"
        assert socket.receive_json()["type"] == "connected"
        joined = client.post(f"{root}/join", headers=member, json={"group_code": group["code"].lower()})
        assert joined.json()["status"] == "PENDING", joined.text
        assert socket.receive_json()["type"] == "group_join_requested"
        assert client.get(f"{path}/chat", headers=member).status_code == 404
        approved = client.patch(f"{path}/members/{member_id}", headers=owner, json={"status": "APPROVED"})
        assert approved.json()["status"] == "APPROVED"
        assert socket.receive_json()["type"] == "group_membership_updated"
        body = {"request_id": str(uuid4()), "title": "Groceries", "category": "FOOD", "amount": 101,
                "participants": [owner_id, member_id], "custom_shares": {owner_id: 41, member_id: 60}}
        assert client.post(f"{path}/expenses", headers=owner, json=body).status_code == 422
        bank = {"bank_bin": "970436", "bank_account_no": "0000000123", "bank_account_name": "TEST ONLY"}
        for headers in (owner, member):
            assert client.put("/api/v1/users/me/bank-info", headers=headers, json=bank).json() == bank
            assert client.put(f"{path}/members/me/qr", headers=headers, json=bank).json() == bank
        expense = client.post(f"{path}/expenses", headers=owner, json=body)
        assert expense.status_code == 201, expense.text
        assert client.post(f"{path}/expenses", headers=owner, json=body).json() == expense.json()
        ledger = client.get(f"{path}/expenses", headers=member).json()
        assert ledger[0]["creditor_id"] == owner_id
        assert {s["user_id"]: s["amount"] for s in ledger[0]["shares"]} == {owner_id: 41, member_id: 60}
        assert client.get(f"{path}/debts", headers=outsider).status_code == 404
        debts = client.get(f"{path}/debts", headers=member).json()
        assert len(debts) == 1 and debts[0]["amount"] == 60 and debts[0]["creditor_id"] == owner_id
        ids = {"debt_ids": [debts[0]["id"]]}
        assert client.get(f"{path}/debts/summary", headers=outsider).status_code == 404
        assert client.get(f"{path}/debts/summary", headers=member).json() == {"owed": 60, "receivable": 0}
        assert client.get(f"{path}/debts/summary", headers=owner).json() == {"owed": 0, "receivable": 60}
        qr = client.post(f"{path}/debts/qr", headers=member, json=ids).json()
        assert qr["bank"] == bank and qr["transfer_description"] in qr["qr_url"]
        assert qr["amount"] == 60 and "amount=60" in qr["qr_url"]
        assert client.post(f"{path}/debts/remind", headers=owner, json=ids).json() == {"notified": 1}
        assert client.post(f"{path}/debts/remind", headers=owner, json=ids).json() == {"notified": 0}
        assert client.portal.call(project_reminder, client.app.state.resources, client.app.state.ws)
        reminder = socket.receive_json()["message"]
        assert reminder["kind"] == "DEBT_REMINDER" and reminder["debtor_ids"] == [member_id]
        assert not client.portal.call(project_reminder, client.app.state.resources, client.app.state.ws)
        assert client.post(f"{path}/chat", headers=member, json={"text": "Hello"}).status_code == 201
        assert socket.receive_json()["type"] == "group_chat"
        upload_id = str(uuid4())
        ticket = {"upload_id": upload_id, "url": "http://127.0.0.1:9000/dorm/staging",
                  "expires_in": 300, "headers": {"Content-Type": "image/jpeg", "Content-Length": "100"}}
        with patch("app.api.routes_group_finance.upload_ticket", AsyncMock(return_value=ticket)):
            upload = client.post(f"{path}/proofs/upload", headers=member, json={**ids, "size": 100, "content_type": "image/jpeg"})
            assert upload.json() == ticket
        with patch("app.api.routes_group_finance.finalize", AsyncMock(return_value="objects/test")) as finalize:
            submission = {**ids, "upload_id": upload_id, "note": "  Rent split  "}
            assert client.post(f"{path}/proofs", headers=member, json={**submission, "note": "x" * 301}).status_code == 422
            result = client.post(f"{path}/proofs", headers=member, json=submission)
            assert result.status_code == 201, result.text
            assert client.post(f"{path}/proofs", headers=member, json=submission).json() == result.json()
            assert finalize.await_count == 1
        proof_id = result.json()["id"]
        assert client.portal.call(project_one, client.app.state.resources, client.app.state.ws)
        assert socket.receive_json()["message"]["status"] == "PENDING"
        assert socket.receive_json()["type"] == "group_proof_submitted"
        receipt = f"{path}/proofs/{proof_id}"
        assert client.get(receipt, headers=outsider).status_code == 404
        wrong_group_receipt = f"{root}/{other_group['id']}/proofs/{proof_id}"
        assert client.get(wrong_group_receipt, headers=outsider).status_code == 404
        with patch("app.api.routes_group_finance.download", AsyncMock(return_value=ticket["url"])):
            details = client.get(receipt, headers=owner).json()
            assert details["url"] == ticket["url"] and details["note"] == "Rent split"
        assert client.patch(f"{receipt}/verify", headers=member, json={"decision": "APPROVE"}).status_code == 403
        assert client.patch(f"{receipt}/verify", headers=owner, json={"decision": "APPROVE"}).json() == {"status": "PAID"}
        assert client.portal.call(project_one, client.app.state.resources, client.app.state.ws)
        assert socket.receive_json()["message"]["status"] == "PAID"
        assert client.get(f"{path}/debts", headers=member).json()[0]["status"] == "PAID"
        assert client.get(f"{path}/debts/summary", headers=member).json()["owed"] == 0
        chat = client.get(f"{path}/chat", headers=member).json()
        assert {item["kind"] for item in chat} == {"TEXT", "PAYMENT_PROOF", "DEBT_REMINDER"}

    repeat = {**body, "request_id": str(uuid4())}
    assert client.post(f"{path}/expenses", headers=owner, json=repeat).status_code == 201
    open_debt = next(row for row in client.get(f"{path}/debts", headers=member).json() if row["status"] == "UNPAID")
    submit = {"debt_ids": [open_debt["id"]], "upload_id": str(uuid4())}
    with patch("app.api.routes_group_finance.finalize", AsyncMock(return_value="objects/second")) as finalize:
        second = client.post(f"{path}/proofs", headers=member, json=submit).json()
        verify = f"{path}/proofs/{second['id']}/verify"
        assert client.patch(verify, headers=owner, json={"decision": "REJECT"}).status_code == 422
        assert client.patch(verify, headers=owner, json={"decision": "REJECT",
                            "reject_reason": "Unreadable receipt"}).json()["status"] == "REJECTED"
        assert client.post(f"{path}/proofs", headers=member, json=submit).json()["status"] == "REJECTED"
        assert finalize.await_count == 1
    assert client.post(f"{root}/join", headers=outsider, json={"group_code": group["code"]}).status_code == 200
    assert client.patch(f"{path}/members/{outsider_id}", headers=owner, json={"status": "APPROVED"}).status_code == 200
    assert client.patch(verify, headers=outsider, json={"decision": "APPROVE"}).status_code == 403
    reopened = client.get(f"{path}/debts", headers=member).json()[0]
    assert reopened["status"] == "UNPAID" and reopened["proof_id"] is None
    for headers, incoming, outgoing in [(member, 60, 0), (owner, 0, 60), (outsider, 0, 0)]:
        report = client.get("/api/v1/analytics/summary", headers=headers).json()
        assert (report["owing"], report["owed"], report["overdue"]) == (incoming, outgoing, 0)


def test_analytics_budget_and_advisor_are_private_and_fall_back_locally(client):
    _, actor = account(client, "alex")
    _, other = account(client, "sam")
    prefix = "/api/v1"
    assert client.get(f"{prefix}/auth/roommates", headers=actor).status_code == 404
    assert client.get(f"{prefix}/finance/debts", headers=actor).status_code == 404
    assert client.options(f"{prefix}/groups/1/members/2", headers={"Origin": "http://127.0.0.1:1420",
        "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization"}).status_code == 200
    today = today_local()
    for amount, category in [(70_000, "FOOD"), (30_000, "TRANSPORT")]:
        result = client.post(f"{prefix}/personal-expenses", headers=actor, json={"amount": amount, "category": category,
                             "type": "EXPENSE", "spent_at": today.isoformat()})
        assert result.status_code == 201, result.text
        assert client.delete(f"{prefix}/personal-expenses/{result.json()['id']}", headers=other).status_code == 404
    budget = {"month": today.replace(day=1).isoformat(), "category": "FOOD", "monthly_limit": 100_000}
    assert client.put(f"{prefix}/budgets", headers=actor, json=budget).json() == budget
    report = client.get(f"{prefix}/analytics/summary?period=month&scope=combined", headers=actor).json()
    assert report["personal"] == 100_000 and len(report["trend"]) == today.day and report["budgets"][0]["spent"] == 70_000
    assert client.get(f"{prefix}/analytics/summary", headers=other).json()["personal"] == 0
    payload = {"text": "How much have I spent?"}
    with patch("app.api.routes_advisor.provider_reply", AsyncMock(return_value=None)) as provider:
        local = client.post(f"{prefix}/ai-chat/message", headers=actor, json=payload).json()
        assert local["source"] == "local" and local["metrics"]["personal"] == 100_000
        assert local["quick_actions"][0]["category"] == "TRANSPORT"
        assert provider.await_count == 0
        assert client.post(f"{prefix}/ai-chat/message", headers=actor,
                           json={**payload, "use_external": True}).json()["source"] == "local"
        provider.assert_awaited_once()
    assert len(client.get(f"{prefix}/ai-chat/history", headers=actor).json()) == 2
    assert client.get(f"{prefix}/ai-chat/history", headers=other).json() == []


def test_gemini_provider_uses_configured_model_and_returns_response():
    config = SimpleNamespace(gemini_api_key=SecretStr("synthetic-key"), gemini_model="test-flash")
    http = Mock()
    transport = AsyncMock(return_value="Provider reply")
    with patch("app.services.advisor.settings", return_value=config), patch("app.services.advisor.request_advice", transport):
        assert asyncio.run(provider_reply("Summarize", {"personal": 500}, http, CircuitBreaker())) == "Provider reply"
    transport.assert_awaited_once_with(http, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                                      "synthetic-key", "test-flash", "Summarize", {"personal": 500})



def test_private_transactions_and_validation(client):
    _, owner = account(client, "private-owner")
    _, other = account(client, "private-other")
    url = "/api/v1/personal-expenses"
    data = {"type": "EXPENSE", "category": "FOOD", "amount": 20, "spent_at": str(today_local())}
    entry = client.post(url, headers=owner, json=data).json()
    assert client.get(url, headers=other).json() == []
    assert client.put(f"{url}/{entry['id']}", headers=other, json=data).status_code == 404
    assert client.delete(f"{url}/{entry['id']}", headers=other).status_code == 404
    assert client.post(url, headers=owner, json={**data, "amount": -1}).status_code == 422
    month = str(today_local().replace(day=1))
    budgets = "/api/v1/budgets"
    budget = {"category": "FOOD", "monthly_limit": 80, "month": month}
    assert client.put(budgets, headers=owner, json=budget).status_code == 200
    assert client.get(budgets, headers=other, params={"month": month}).json() == []
    assert client.get(budgets, headers=owner, params={"month": month}).json() == [budget]


def test_websocket_requires_authentication(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws") as socket:
            socket.receive_json()
    assert error.value.code == 1008


def test_registration_validation_does_not_echo_password(client):
    password = "onewordonlylowercase"
    response = client.post("/api/v1/auth/register", json={
        "username": "security-test", "password": password, "full_name": "Test"})
    assert response.status_code == 422
    assert password not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"



def test_auth_throttles_login_and_registration(client):
    login = "/api/v1/auth/login"
    credentials = {"username": "missing-user", "password": "wrong-password"}
    for _ in range(5):
        assert client.post(login, json=credentials).status_code == 401
    blocked = client.post(login, json=credentials)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 60

    register = "/api/v1/auth/register"
    credentials["full_name"] = "Test User"
    assert client.post(register, json=credentials).status_code == 201
    for _ in range(4):
        duplicate = client.post(register, json=credentials)
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "Registration could not be completed"}
    assert client.post(register, json=credentials).status_code == 429


@pytest.mark.parametrize("data, limit", [(b"Not an image", 299_999), (b"x" * 9, 8)])
def test_storage_rejects_invalid_images_before_permanent_write(data, limit):
    from io import BytesIO
    from fastapi import HTTPException
    from app.services.storage_service import finalize

    s3 = Mock()
    s3.head_object.return_value = {"ContentType": "image/jpeg", "ContentLength": len(data), "ETag": "test"}
    s3.get_object.return_value = {"Body": BytesIO(data)}
    with patch("app.services.storage_service.client", return_value=s3), pytest.raises(HTTPException) as error:
        asyncio.run(finalize(1, "group-receipts/2", uuid4(), {"image/jpeg"}, limit))
    assert error.value.status_code == 400
    s3.put_object.assert_not_called()
    s3.copy_object.assert_not_called()


@pytest.mark.parametrize("format_name", ["JPEG", "PNG", "WEBP"])
def test_storage_reencodes_valid_images(format_name):
    from io import BytesIO
    from PIL import Image
    from app.services.storage_service import download, finalize

    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, format=format_name)
    data = image.getvalue()
    mime = f"image/{format_name.lower()}"
    upload_id = uuid4()
    s3 = Mock()
    s3.head_object.return_value = {"ContentType": mime, "ContentLength": len(data), "ETag": "test"}
    s3.get_object.return_value = {"Body": BytesIO(data)}
    with patch("app.services.storage_service.client", return_value=s3):
        key = asyncio.run(finalize(1, "group-receipts/2", upload_id, {mime}, 299_999))
    assert s3.get_object.call_args.kwargs["IfMatch"] == "test"
    assert key.startswith("receipts/2/")
    with Image.open(BytesIO(s3.put_object.call_args.kwargs["Body"])) as clean:
        assert clean.format == format_name and clean.size == (2, 2)
    assert s3.put_object.call_args.kwargs["ContentType"] == mime
    assert s3.put_object.call_args.kwargs["ContentDisposition"] == "attachment"
    with patch("app.services.storage_service.presign", AsyncMock(return_value="signed")) as sign:
        assert asyncio.run(download(key, namespace="group-receipts/2", user_id=1)) == "signed"
        sign.assert_awaited_once_with("get_object", {"Key": key})


def test_unhandled_backend_error_is_sanitized(client):
    _, actor = account(client, "error-check")
    with patch("app.api.routes_advisor.summarize", AsyncMock(side_effect=RuntimeError("internal sentinel"))):
        response = client.get("/api/v1/analytics/summary", headers=actor)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["referrer-policy"] == "no-referrer"



def test_group_events_are_checked_at_websocket_delivery(client):
    _, owner = account(client, "event-owner")
    outsider_id, outsider = account(client, "event-outsider")
    group = client.post("/api/v1/groups", headers=owner, json={"name": "Event group"}).json()
    with client.websocket_connect("/ws", headers=outsider) as socket:
        assert socket.receive_json()["type"] == "connected"
        manager = client.app.state.ws
        socket.portal.call(manager.publish, {"type": "group_chat", "group_id": group["id"]}, [outsider_id])
        socket.portal.call(manager.publish, {"type": "resync"}, [outsider_id])
        assert socket.receive_json() == {"type": "resync"}



def test_monthly_summary_reuses_grouped_personal_spending(client):
    from sqlalchemy import event

    _, actor = account(client, "monthly-query")
    today = today_local()
    assert client.put("/api/v1/budgets", headers=actor, json={
        "category": "FOOD", "monthly_limit": 100_000, "month": str(today.replace(day=1)),
    }).status_code == 200
    assert client.post("/api/v1/personal-expenses", headers=actor, json={
        "type": "EXPENSE", "category": "FOOD", "amount": 25_000, "spent_at": str(today),
    }).status_code == 201

    engine = client.app.state.resources.sql.kw["bind"].sync_engine
    personal_queries = []

    def record(_connection, _cursor, statement, *_args):
        if "FROM personal_expenses" in statement:
            personal_queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = client.post("/api/v1/ai-chat/message", headers=actor, json={"text": "Budget check"})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result.status_code == 200
    assert result.json()["metrics"]["personal"] == 25_000
    assert "at 25% of its monthly limit" in result.json()["reply"]
    assert len(personal_queries) == 1



def test_performance_indexes_are_added_to_existing_tables():
    from sqlalchemy import create_engine, inspect
    from app.core.database import initialize_mysql_schema
    from app.models.living import PERFORMANCE_INDEXES

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            connection.exec_driver_sql("ALTER TABLE group_payment_proofs DROP COLUMN note")
            for index in PERFORMANCE_INDEXES:
                index.drop(connection)
            initialize_mysql_schema(connection)
            initialize_mysql_schema(connection)
            assert "note" in {col["name"] for col in inspect(connection).get_columns("group_payment_proofs")}
            for index in PERFORMANCE_INDEXES:
                existing = {item["name"]: item["column_names"]
                            for item in inspect(connection).get_indexes(index.table.name)}
                assert existing[index.name] == [column.name for column in index.columns]
    finally:
        engine.dispose()


def test_group_chat_cursor_keeps_tie_order_and_group_scope(client):
    from functools import partial
    from app.models.mysql_models import now

    _, actor = account(client, "cursor-owner")
    groups = "/api/v1/groups"
    first = client.post(groups, headers=actor, json={"name": "First"}).json()["id"]
    second = client.post(groups, headers=actor, json={"name": "Second"}).json()["id"]
    stamp = now()
    messages = client.app.state.resources.mongo.group_messages
    for key, group in [("message:a", first), ("message:b", first), ("message:c", first), ("message:z", second)]:
        row = {"_id": key, "group_id": group, "created_at": stamp}
        client.portal.call(partial(messages.replace_one, {"_id": key}, row, upsert=True))
    path = f"{groups}/{first}/chat"
    result = client.get(path, headers=actor, params={"before": "message:c"})
    assert result.status_code == 200
    assert [row["id"] for row in result.json()] == ["message:a", "message:b"]
    assert client.get(path, headers=actor, params={"before": "message:z"}).status_code == 404


def test_group_scoped_websocket_requires_approved_membership(client):
    from starlette.websockets import WebSocketDisconnect

    _, owner = account(client, "socket-owner")
    _, outsider = account(client, "socket-outsider")
    group = client.post("/api/v1/groups", headers=owner, json={"name": "Socket group"}).json()
    path = f"/ws?group_id={group['id']}"
    with client.websocket_connect(path, subprotocols=["bearer", owner["Authorization"].split()[1]]) as socket:
        assert socket.accepted_subprotocol == "bearer"
        assert socket.receive_json()["type"] == "connected"
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path, subprotocols=["bearer", outsider["Authorization"].split()[1]]) as socket:
            socket.receive_json()
    assert closed.value.code == 1008


@pytest.mark.parametrize("format_name", ["JPEG", "PNG", "WEBP"])
def test_receipt_sanitizer_preserves_portrait_orientation(format_name):
    from io import BytesIO

    from PIL import Image
    from app.services.storage_service import sanitize_image

    source = BytesIO()
    with Image.new("RGB", (60, 40), "white") as image:
        exif = image.getexif()
        exif[274] = 6
        image.save(source, format=format_name, exif=exif)
    _, _, cleaned = sanitize_image(source.getvalue())
    with Image.open(BytesIO(cleaned)) as receipt:
        assert receipt.size == (40, 60)
        assert not receipt.getexif()
