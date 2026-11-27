import pytest

from app.api.routes_advisor import budget_status
from app.api import routes_advisor
from app.api import routes_group_chat
from app.services.chat_expense import parse_expense
from test_living import account, client  # noqa: F401


@pytest.mark.parametrize(("value", "amount"), [("240k", 240000), ("1.5tr", 1500000), ("150.000đ", 150000)])
def test_fallback_amount_and_participation(value, amount):
    members = [(1, "Án", "an"), (2, "Bình", "binh"), (3, "Lan", "lan")]
    draft = parse_expense(f"/chi {value} tien lau, An tra, Binh khong an", members, 3)
    assert draft["total_amount"] == amount
    assert [row["status"] for row in draft["participants"]] == ["paid", "excluded", "unpaid"]


@pytest.mark.parametrize(("spent", "status"), [(79, "normal"), (80, "warning"), (99, "warning"), (100, "exceeded")])
def test_budget_thresholds(spent, status):
    assert budget_status(spent, 100) == status


def test_gemini_draft_confirm_and_budget(client, monkeypatch):
    owner_id, owner = account(client, "anh")
    member_id, member = account(client, "binh")
    _, outsider = account(client, "visitor")
    root = "/api/v1/groups"
    group = client.post(root, headers=owner, json={"name": "Test group"}).json()
    path = f"{root}/{group['id']}"
    client.post(f"{root}/join", headers=member, json={"group_code": group["code"]})
    assert client.patch(f"{path}/members/{member_id}", headers=owner, json={"status": "APPROVED"}).status_code == 200
    bank = {"bank_bin": "970436", "bank_account_no": "0000000123", "bank_account_name": "TEST ONLY"}
    for headers in (owner, member):
        assert client.put(f"{path}/members/me/qr", headers=headers, json=bank).status_code == 200

    async def gemini(message, context, http, breaker, **options):
        assert options == {"structured": True, "gemini_only": True}
        return '{"title": "Tien lau Gemini"}'

    async def offline(*args, **kwargs):
        return None

    monkeypatch.setattr(routes_advisor, "provider_reply", offline)
    monkeypatch.setattr(routes_group_chat, "provider_reply", gemini)
    monkeypatch.setattr(client.app.state, "http", object(), raising=False)
    monkeypatch.setattr(client.app.state, "advisor_breaker", object(), raising=False)
    draft_path = f"{path}/chat/parse-expense"
    body = {"text": "/chi 240k tien lau, Anh tra"}
    assert client.post(draft_path, headers=outsider, json=body).status_code == 404
    result = client.post(draft_path, headers=owner, json=body)
    assert result.status_code == 201, result.text
    draft = result.json()
    assert draft["preview"]["title"] == "Tien lau Gemini"
    confirmation = f"{path}/chat/confirm-expense/{draft['draft_id']}"
    assert client.post(confirmation, headers=outsider).status_code == 404
    actual = client.post(confirmation, headers=member)
    assert actual.status_code == 200, actual.text
    assert client.post(confirmation, headers=owner).json() == actual.json()
    expenses = client.get(f"{path}/expenses", headers=member).json()
    assert len(expenses) == 1
    assert expenses[0]["creditor_id"] == owner_id
    assert [row["status"] for row in expenses[0]["participants"]] == ["paid", "unpaid"]
    assert client.get(f"{path}/chat", headers=member).json()[0]["status"] == "confirmed"

    assert client.patch(f"{path}/budget-limit", headers=member, json={"monthly_limit": 300000}).status_code == 403
    assert client.get(f"{path}/advisor/insights", headers=outsider).status_code == 404
    for cap, status in [(300000, "warning"), (240000, "exceeded")]:
        assert client.patch(f"{path}/budget-limit", headers=owner, json={"monthly_limit": cap}).status_code == 200
        insights = client.get(f"{path}/advisor/insights", headers=member).json()
        assert insights["current_month_spend"] == 240000
        assert insights["status"] == status
