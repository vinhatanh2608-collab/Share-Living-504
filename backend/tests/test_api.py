import asyncio
import os
import unittest
from types import SimpleNamespace
from pathlib import Path
from secrets import token_hex
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

os.environ.setdefault("JWT_SECRET", token_hex(32))

for key, value in dotenv_values(Path(__file__).parent.parent / ".env.example").items():
    os.environ.setdefault(key, value)

from app.main import create_app
from app.core.security import issue
from app.models.mysql_models import Base, User, Expense, Debt, Proof, Outbox, now
from app.services.ws_manager import Manager

app = create_app(legacy_room=True)


class API(unittest.TestCase):
    def setUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        self.sql = async_sessionmaker(self.engine, expire_on_commit=False)
        app.state.resources = SimpleNamespace(sql=self.sql)
        asyncio.run(self.seed())
        self.client = TestClient(app)
        self.addCleanup(lambda: asyncio.run(self.engine.dispose()))
        self.addCleanup(self.client.close)

    async def seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # SQLite only auto-generates integer primary keys with an exact INTEGER type.
            await conn.exec_driver_sql("DROP TABLE outbox")
            await conn.exec_driver_sql(
                "CREATE TABLE outbox(id INTEGER PRIMARY KEY, payload JSON NOT NULL, "
                "delivered BOOLEAN NOT NULL, attempts INTEGER NOT NULL, next_attempt DATETIME NOT NULL)")
        from app.core.config import settings
        async with self.sql.begin() as session:
            session.add_all([
                User(id=1, username=settings().owner_username, name="Owner", password_hash="unused"),
                User(id=2, username="debtor", name="Debtor", password_hash="unused"),
                User(id=3, username="creditor", name="Creditor", password_hash="unused"),
                Expense(id=1, creditor_id=3, request_id="one", request_hash="unused",
                        title="Electricity", amount=123, due_at=now()),
                Debt(id=1, expense_id=1, debtor_id=2, amount=123, status="pending"),
                Proof(id=1, debt_id=1, upload_id="one", object_key="private", status="pending")])

    def approve(self, user):
        return self.client.post("/finance/proofs/1/decision", json={"approve": True},
                                headers={"Authorization": f"Bearer {issue(user)}"})

    def test_non_creditors_cannot_approve(self):
        self.assertEqual(self.approve(1).status_code, 403)
        self.assertEqual(self.approve(2).status_code, 403)
        self.assertEqual(asyncio.run(self.read_state()), ("pending", []))

    def test_creditor_approves_once_and_records_outbox(self):
        self.assertEqual(self.approve(3).json(), {"id": 1, "status": "paid"})
        self.assertEqual(self.approve(3).status_code, 200)
        state, events = asyncio.run(self.read_state())
        self.assertEqual(state, "paid")
        self.assertEqual(events, [{"proof_id": 1, "debt_id": 1, "status": "paid",
                                   "debtor_id": 2, "creditor_id": 3}])

    async def read_state(self):
        async with self.sql() as session:
            debt = await session.get(Debt, 1)
            return debt.status, list(await session.scalars(select(Outbox.payload)))

    def test_unauthenticated_request_cannot_list_debts(self):
        self.assertEqual(self.client.get("/finance/debts").status_code, 401)

    def test_openapi_has_finance_and_study(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/finance/proofs/{proof_id}/decision", paths)
        self.assertIn("/study/notes/{name}", paths)


class WebSocketFanout(unittest.TestCase):
    def test_proof_event_stays_private(self):
        manager = Manager()
        allowed, other = manager.attach(1), manager.attach(2)
        manager.publish({"type": "payment_card"}, [1])
        self.assertEqual(allowed.get_nowait(), {"type": "payment_card"})
        self.assertTrue(other.empty())
