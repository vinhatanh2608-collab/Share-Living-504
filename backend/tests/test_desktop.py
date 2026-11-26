import asyncio
from unittest.mock import patch
import test_api
from uuid import uuid4
from app.core.security import issue
from app.models.mysql_models import Debt, Expense, now


class DesktopContract(test_api.API):
    def test_custom_split_rejects_wrong_total(self):
        result = self.client.post("/api/v1/finance/expenses", headers={
            "Authorization": f"Bearer {issue(1)}"}, json={
            "title": "Internet", "amount": 100, "participants": [1, 2],
            "custom_shares": {"1": 50, "2": 40}, "due_at": "2027-01-01T00:00:00Z",
            "request_id": str(uuid4())})
        self.assertEqual(result.status_code, 422)

    def test_custom_split_persists_exact_personal_amount(self):
        headers = {"Authorization": f"Bearer {issue(1)}"}
        result = self.client.post("/api/v1/finance/expenses", headers=headers, json={
            "title": "Internet", "amount": 100, "participants": [1, 2],
            "custom_shares": {"1": 30, "2": 70}, "due_at": "2027-01-01T00:00:00Z",
            "request_id": str(uuid4())})
        self.assertEqual(result.status_code, 201)
        debts = self.client.get("/api/v1/finance/debts", headers=headers).json()
        self.assertEqual([d["amount"] for d in debts if d["expense_id"] == result.json()["id"]], [70])

    def test_batch_refuses_another_roommates_debt(self):
        result = self.client.post("/api/v1/finance/proof-batches", headers={
            "Authorization": f"Bearer {issue(3)}"}, json={
            "debt_ids": [1], "upload_id": str(uuid4())})
        self.assertEqual(result.status_code, 404)

    def test_batch_rejects_an_unavailable_debt_without_partial_submission(self):
        asyncio.run(self.seed_batch("pending"))
        headers = {"Authorization": f"Bearer {issue(2)}"}
        with patch("app.api.routes_finance.finalize", return_value="stored-receipt") as storage:
            result = self.client.post("/api/v1/finance/proof-batches", headers=headers,
                                      json={"debt_ids": [2, 3], "upload_id": str(uuid4())})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(storage.await_count, 0)
        self.assertEqual(asyncio.run(self.batch_states()), ["open", "pending"])

    def test_batch_moves_every_debt_to_pending_and_replays_without_copying(self):
        asyncio.run(self.seed_batch("open"))
        headers = {"Authorization": f"Bearer {issue(2)}"}
        body = {"debt_ids": [2, 3], "upload_id": str(uuid4())}
        with patch("app.api.routes_finance.finalize", return_value="stored-receipt") as storage:
            first = self.client.post("/api/v1/finance/proof-batches", headers=headers, json=body)
            second = self.client.post("/api/v1/finance/proof-batches", headers=headers,
                                      json={**body, "debt_ids": [3, 2]})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.json(), first.json())
        self.assertEqual([(r["debt_id"], r["status"]) for r in first.json()], [(2, "pending"), (3, "pending")])
        self.assertEqual(storage.await_count, 1)
        self.assertEqual(asyncio.run(self.batch_states()), ["pending", "pending"])
        self.assertEqual(len(asyncio.run(self.read_state())[1]), 2)

    async def seed_batch(self, state):
        async with self.sql.begin() as session:
            session.add_all([Expense(id=i, creditor_id=3, request_id=f"batch{i}", request_hash="unused",
                                     title="Test expense", amount=10, due_at=now()) for i in [2, 3]])
            session.add_all([Debt(id=i, expense_id=i, debtor_id=2, amount=10, status=s)
                             for i, s in [(2, "open"), (3, state)]])

    async def batch_states(self):
        async with self.sql() as session:
            return [(await session.get(Debt, i)).status for i in [2, 3]]


def load_tests(loader, _standard, _pattern):
    return test_api.unittest.TestSuite(DesktopContract(name) for name in loader.getTestCaseNames(DesktopContract)
                                       if name in DesktopContract.__dict__)
