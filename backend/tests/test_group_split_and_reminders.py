from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from test_living import account, client


@pytest.fixture
def household(client):
    people = [account(client, name) for name in ("minh", "lan", "anh", "binh")]
    _, owner = people[0]
    group = client.post("/api/v1/groups", headers=owner, json={"name": "Test household"}).json()
    base = f"/api/v1/groups/{group['id']}"
    for uid, headers in people[1:]:
        client.post("/api/v1/groups/join", headers=headers, json={"group_code": group["code"]}).raise_for_status()
        client.patch(f"{base}/members/{uid}", headers=owner, json={"status": "APPROVED"}).raise_for_status()
    for uid, headers in people:
        client.put(f"{base}/members/me/qr", headers=headers, json={"bank_bin": "970436",
                   "bank_account_no": f"0000{uid}", "bank_account_name": "TEST MEMBER"}).raise_for_status()
    return base, group["id"], people


def expense(client, base, actor, amount, **fields):
    result = client.post(f"{base}/expenses", headers=actor, json={
        "request_id": str(uuid4()), "title": "Tien dien", "category": "HOUSING",
        "amount": amount, **fields})
    result.raise_for_status()
    return result.json()["id"]


def snapshot(client, group_id):
    from app.services.debt_simplifier import simplify_group

    async def read():
        async with client.app.state.resources.sql() as sql:
            return await simplify_group(sql, group_id)
    return client.portal.call(read)


def test_mixed_contributions_reconcile_to_bill(client, household):
    base, group_id, people = household
    participants = [{"user_id": str(uid), "status": status, "paid_amount": paid}
                    for (uid, _), status, paid in zip(people, ["paid", "paid", "unpaid", "excluded"],
                                                      [71, 29, 0, 0])]
    expense(client, base, people[0][1], 100, participants=participants)
    report = snapshot(client, group_id)
    positions = {r["user_id"]: r for r in report["member_breakdown"]}
    assert [(positions[uid]["total_contributed"], positions[uid]["total_share"], positions[uid]["net_balance"])
            for uid, _ in people] == [(71, 34, 37), (29, 33, -4), (0, 33, -33), (0, 0, 0)]
    payer, headers = people[0]
    other = people[1][0]
    expense(client, base, headers, 20, payer_id=payer, split_user_ids=[other])
    updated = {r["user_id"]: r for r in snapshot(client, group_id)["member_breakdown"]}
    assert updated[payer]["net_balance"] == 47 and updated[other]["net_balance"] == -14


def test_chain_simplifies_and_respects_approved_receipt(client, household):
    base, group_id, people = household
    (a, debtor), (b, middle), (c, creditor), _ = people
    expense(client, base, middle, 100000, participants=[a])
    expense(client, base, creditor, 100000, participants=[b])
    edge, = snapshot(client, group_id)["transfers"]
    assert (edge["debtor_id"], edge["creditor_id"], edge["amount"]) == (a, c, 100000)
    assert edge["explanation_summary"] and edge["chain_steps"]
    selection = {"transfer": {"creditor_id": c, "amount": edge["amount"]}}
    quote = client.post(f"{base}/debts/qr", headers=debtor, json=selection)
    assert quote.json()["amount"] == 100000
    with patch("app.api.routes_group_finance.finalize", AsyncMock(return_value="receipts/test")):
        submitted = client.post(f"{base}/proofs", headers=debtor, json={**selection, "upload_id": str(uuid4())})
        submitted.raise_for_status()
    assert not snapshot(client, group_id)["transfers"]
    assert client.post(f"{base}/debts/qr", headers=debtor, json=selection).status_code == 409
    raw_id = client.get(f"{base}/debts", headers=debtor).json()[0]["id"]
    assert client.post(f"{base}/debts/qr", headers=debtor, json={"debt_ids": [raw_id]}).status_code == 409
    review = f"{base}/proofs/{submitted.json()['id']}/verify"
    assert client.patch(review, headers=middle, json={"decision": "APPROVE"}).status_code == 403
    client.patch(review, headers=creditor, json={"decision": "APPROVE"}).raise_for_status()
    final = snapshot(client, group_id)
    assert not final["transfers"]
    assert all(r["net_balance"] == 0 for r in final["member_breakdown"])
    assert client.get(f"{base}/debts/summary", headers=debtor).json() == {"owed": 0, "receivable": 0}
    assert client.post(f"{base}/debts/remind", headers=middle, json={"debt_ids": [raw_id]}).json() == {"notified": 0}


def test_update_validates_members_and_preserves_previous_allocation_on_failure(client, household):
    base, _, people = household
    (payer, owner), (member, other), *_ = people
    expense_id = expense(client, base, owner, 11, participants=[member])
    endpoint = f"{base}/expenses/{expense_id}"
    body = {"title": "Lunch", "category": "FOOD", "total_amount": 11, "participants": [
        {"user_id": str(payer), "status": "paid", "paid_amount": 11, "share_amount": 2},
        {"user_id": str(member), "status": "unpaid", "paid_amount": 0},
    ]}
    assert client.put(endpoint, headers=other, json=body).status_code == 404
    client.put(endpoint, headers=owner, json=body).raise_for_status()
    for invalid in ({"user_id": "9999999"}, {"status": "excluded", "share_amount": 3},
                    {"paid_amount": 1}, {"share_amount": 10}, {"share_amount": 0.5}):
        broken = [body["participants"][0], {**body["participants"][1], **invalid}]
        assert client.put(endpoint, headers=owner, json={**body, "participants": broken}).status_code == 422
    entry, = client.get(f"{base}/expenses", headers=owner).json()
    assert [p["net"] for p in entry["participants"]] == [9, -9, 0, 0]


def test_monthly_trigger_is_idempotent(client, household):
    from app.services.monthly_reminders import run_due

    base, group_id, people = household
    (a, debtor), (_, owner_creditor), *_ = people
    expense(client, base, owner_creditor, 120000, participants=[a])
    assert client.post(f"{base}/reminders/run-monthly", headers=owner_creditor).status_code == 403
    assert client.post(f"{base}/reminders/run-monthly", headers=debtor).json()["status"] == "sent"
    groups = client.get("/api/v1/groups", headers=debtor).json()
    period = groups[0]["last_reminded_period"]
    assert period
    import calendar
    year, month = map(int, period.split("-"))
    at = datetime(year, month, calendar.monthrange(year, month)[1], 22, tzinfo=timezone(timedelta(hours=7)))
    client.portal.call(run_due, client.app.state.resources, client.app.state.ws, at)
    chat = client.get(f"{base}/chat", headers=debtor).json()
    assert sum(row["kind"] == "MONTHLY_REMINDER" for row in chat) == 1
    leap_year = year + (4 - year % 4)
    february = datetime(leap_year, 2, calendar.monthrange(leap_year, 2)[1], 20, tzinfo=at.tzinfo)
    async def due(when):
        return await run_due(client.app.state.resources, client.app.state.ws, when)
    assert client.portal.call(due, february - timedelta(minutes=1)) == 0
    assert client.portal.call(due, february) == 1
    assert client.portal.call(due, february) == 0
