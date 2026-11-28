import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import Resources
from app.models.archive import debt_archive, group_archive, personal_archive
from app.models.living import DebtSplit, GroupExpense, PersonalTransaction
from app.services.archive_service import archive_batch
from app.services.cache_service import CacheService
from test_living import account, client  # noqa: F401


class InMemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value.encode()
        return True

    async def setnx(self, key, value):
        return key not in self.values and await self.set(key, value)

    async def setex(self, key, ttl, value):
        assert 0 < ttl <= 60
        return await self.set(key, value)

    async def eval(self, *_):
        # This fixture tests cache integration independently from the Lua counter.
        raise ConnectionError("Redis is unavailable for counters")


def test_cache_hit_and_invalidation_on_expense_write(client, monkeypatch):
    from app.api import routes_advisor

    service = CacheService(None)
    service.client = InMemoryRedis()
    service.rate = lambda *args: asyncio.sleep(0, result=None)
    client.app.state.cache = service
    uid, auth = account(client, "cache-owner")
    observed = []
    original = routes_advisor.summarize

    async def tracked(*args, **kwargs):
        observed.append(args[1])
        return await original(*args, **kwargs)

    monkeypatch.setattr(routes_advisor, "summarize", tracked)
    url = "/api/v1/analytics/summary?scope=personal"
    assert client.get(url, headers=auth).json()["personal"] == 0
    assert client.get(url, headers=auth).json()["personal"] == 0
    assert observed == [uid]
    result = client.post("/api/v1/personal-expenses", headers=auth, json={
        "type": "EXPENSE", "category": "FOOD", "amount": 123,
        "note": "Cache invalidation test", "spent_at": routes_advisor.today_local().isoformat(),
    })
    assert result.status_code == 201, result.text
    assert client.get(url, headers=auth).json()["personal"] == 123
    assert observed == [uid, uid]


def test_unavailable_redis_uses_loader():
    async def run():
        service = CacheService(None)
        service.client = InMemoryRedis()

        async def offline(*args):
            raise ConnectionError("offline")

        service.client.get = offline
        result = await service.get_or_load(["budget"], lambda: asyncio.sleep(0, result={"spent": 7}))
        assert result == {"spent": 7}
        assert await service.rate("api", "user", 3, 60) is None

    asyncio.run(run())


class Session:
    def __init__(self, role, fail=False):
        self.role = role
        self.fail = fail
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def execute(self, query):
        self.commands.append(str(query))
        if self.fail:
            raise TimeoutError
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: {
            "Replica_IO_Running": "Yes", "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 0,
        }))


def test_read_routing_falls_back_and_backs_off():
    async def run():
        resources = Resources.__new__(Resources)
        resources.cfg = settings()
        primary, replica = Session("primary"), Session("replica")
        resources.sql, resources.replica_sql = lambda: primary, lambda: replica
        resources.replica_retry_at = resources.replica_failures = 0

        async def query(session):
            return session.role

        assert await resources.read(query) == "replica"
        assert replica.commands == ["SHOW REPLICA STATUS", "START TRANSACTION READ ONLY"]
        replica.fail = True
        assert await resources.read(query) == "primary"
        assert await resources.read(query) == "primary"
        assert len(replica.commands) == 3
        resources.replica_sql = None
        assert await resources.read(query) == "primary"

    asyncio.run(run())


def test_search_and_list_bounds(client):
    from app.main import create_app

    _, auth = account(client, "bounds-owner")
    url = "/api/v1/groups"
    for name in ["First", "Second"]:
        assert client.post(url, headers=auth, json={"name": name}).status_code == 201
    result = client.get(url, headers=auth, params={"limit": 1}).json()
    assert len(result) == 1
    second = client.get(url, headers=auth, params={"after": result[0]["id"]}).json()
    assert second[0]["name"] == "Second"
    for params in ({"limit": 501}, {"limit": 0}, {"after": -1}):
        assert client.get(url, headers=auth, params=params).status_code == 422
    legacy = create_app(legacy_room=True)
    from app.api.deps import current_user
    legacy.dependency_overrides[current_user] = lambda: object()
    response = TestClient(legacy).get("/api/v1/study/materials", params={"q": "x" * 201})
    assert response.status_code == 422
    assert TestClient(legacy).get("/api/v1/study/materials", params={"limit": 101}).status_code == 422


def test_request_timeout_returns_504_and_cancels_handler():
    from app.main import create_app

    timeout = settings().model_copy(update={"request_timeout_seconds": .02})
    app = create_app(config=timeout)
    observed = []

    @app.get("/slow")
    async def slow(_request: Request):
        try:
            await asyncio.sleep(1)
        finally:
            observed.append("cancelled")

    response = TestClient(app).get("/slow")
    assert response.status_code == 504, response.text
    assert observed == ["cancelled"]


def test_archive_keeps_api_history_and_old_expense_replays(client):
    owner_id, owner = account(client, "archive-owner")
    member_id, member = account(client, "archive-member")
    groups = "/api/v1/groups"
    group = client.post(groups, headers=owner, json={"name": "History"}).json()
    path = f"{groups}/{group['id']}"
    assert client.post(groups + "/join", headers=member, json={"group_code": group["code"]}).status_code < 300
    assert client.patch(f"{path}/members/{member_id}", headers=owner, json={"status": "APPROVED"}).status_code == 200
    bank = {"bank_bin": "970436", "bank_account_no": "0000000123", "bank_account_name": "TEST ONLY"}
    for headers in (owner, member):
        assert client.put(f"{path}/members/me/qr", headers=headers, json=bank).status_code == 200
    expense = {"request_id": str(uuid4()), "title": "History", "category": "FOOD",
               "amount": 90, "participants": [owner_id, member_id]}
    saved = client.post(f"{path}/expenses", headers=owner, json=expense).json()
    personal_path = "/api/v1/personal-expenses"
    personal = {"spent_at": "2020-01-02", "type": "EXPENSE", "amount": 1,
                "category": "FOOD", "note": "Old"}
    record = client.post(personal_path, headers=owner, json=personal)
    assert record.status_code == 201, record.text
    resources = client.app.state.resources

    async def settle():
        async with resources.sql.begin() as sql:
            await sql.execute(update(GroupExpense).where(GroupExpense.id == saved["id"]).values(
                created_at=datetime(2020, 1, 2)))
            await sql.execute(update(DebtSplit).where(DebtSplit.expense_id == saved["id"]).values(status="PAID"))

    client.portal.call(settle)
    report = f"{path}/monthly-report/export?period=2020-01&format=csv"
    urls = [f"{path}/expenses", f"{path}/debts", f"{path}/debts/simplified", report, personal_path, "/api/v1/analytics/summary?scope=group"]
    before = [client.get(url, headers=owner) for url in urls]
    assert all(response.status_code == 200 for response in before)
    cutoff = datetime(2021, 1, 1)
    assert client.portal.call(archive_batch, resources, cutoff) == (1, 1)
    assert client.portal.call(archive_batch, resources, cutoff) == (0, 0)

    async def check_tables():
        async with resources.sql() as sql:
            for model in (GroupExpense, DebtSplit, PersonalTransaction):
                assert not list(await sql.scalars(select(model)))
            for table in (group_archive, debt_archive, personal_archive):
                assert len((await sql.execute(select(table))).all()) == 1

    client.portal.call(check_tables)
    assert [client.get(url, headers=owner).content for url in urls] == [response.content for response in before]
    assert client.post(f"{path}/expenses", headers=owner, json=expense).json() == saved
    item_path = f"{personal_path}/{record.json()['id']}"
    modified = client.put(item_path, headers=owner, json={**personal, "note": "Corrected"})
    assert modified.json()["note"] == "Corrected"
    assert client.delete(item_path, headers=owner).status_code == 204
    assert client.get(personal_path, headers=owner).json() == []
