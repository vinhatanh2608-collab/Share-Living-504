"""Exercise the running development stack with generated test accounts and a one-pixel receipt."""
import asyncio
from base64 import b64decode
import json
import os
from io import BytesIO
from PIL import Image
from secrets import token_hex, token_urlsafe
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from websockets.asyncio.client import connect

from app.api.routes_advisor import today_local

API = os.getenv("SMOKE_API_URL", "http://127.0.0.1:8000").rstrip("/")
ORIGIN = "http://127.0.0.1:1420"
JPEG = b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/"
    "2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/"
    "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxM"
    "XGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ"
    "3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3"
    "eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD8qqKKKAP/2Q=="
)


def request(method, path, token=None, data=None):
    headers = {"Origin": ORIGIN}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    url = f"{API}{path}" if path.startswith("/health/") else f"{API}/api/v1{path}"
    try:
        with urlopen(Request(url, method=method, headers=headers,
                             data=json.dumps(data).encode() if data is not None else None), timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path} returned {exc.code}: {exc.read(512).decode(errors='replace')}") from None


def account(prefix, run_id):
    username = f"smoke_{prefix}_{run_id}"
    password = token_urlsafe(24)
    profile = request("POST", "/auth/register", data={"username": username, "password": password, "full_name": username})
    login = request("POST", "/auth/login", data={"username": username, "password": password})
    return profile["id"], login["access_token"]


async def wait_event(socket, kind):
    async with asyncio.timeout(15):
        while True:
            event = json.loads(await socket.recv())
            if event["type"] == kind:
                return event


def check_storage(ticket):
    headers = {"Origin": ORIGIN, "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"}
    try:
        with urlopen(Request(ticket["url"], method="OPTIONS", headers=headers), timeout=10) as response:
            assert response.headers.get("Access-Control-Allow-Origin") in {ORIGIN, "*"}
        with urlopen(Request(ticket["url"], method="PUT", headers={**ticket["headers"], "Origin": ORIGIN},
                             data=JPEG), timeout=10) as response:
            assert response.status in {200, 204}
            assert response.headers.get("Access-Control-Allow-Origin") in {ORIGIN, "*"}
    except HTTPError as exc:
        raise RuntimeError(f"Receipt CORS preflight or PUT failed with HTTP {exc.code}: "
                           f"{exc.read(400).decode(errors='replace')}") from None


async def main():
    assert request("GET", "/health/ready")["status"] == "ok"
    with urlopen(ORIGIN, timeout=5) as frontend:
        assert "Shared Living" in frontend.read().decode()
    run_id = token_hex(5)
    owner_id, owner = account("owner", run_id)
    member_id, member = account("member", run_id)
    root = "/groups"
    group = request("POST", root, owner, {"name": f"Smoke test {run_id}"})
    path = f"{root}/{group['id']}"
    ws = API.replace("http", "ws", 1) + "/ws"
    async with (
        connect(ws, origin=ORIGIN, additional_headers={"Authorization": f"Bearer {owner}"}) as owner_ws,
        connect(ws, origin=ORIGIN, additional_headers={"Authorization": f"Bearer {member}"}) as member_ws,
    ):
        await wait_event(owner_ws, "connected")
        await wait_event(member_ws, "connected")
        assert request("POST", f"{root}/join", member, {"group_code": group["code"]})["status"] == "PENDING"
        assert (await wait_event(owner_ws, "group_join_requested"))["group_id"] == group["id"]
        request("PATCH", f"{path}/members/{member_id}", owner, {"status": "APPROVED"})
        await wait_event(member_ws, "group_membership_updated")
        bank = {"bank_bin": "970436", "bank_account_no": "0000000123", "bank_account_name": "SYNTHETIC TEST"}
        for token in (owner, member):
            request("PUT", "/users/me/bank-info", token, bank)
            request("PUT", f"{path}/members/me/qr", token, bank)
        request("POST", f"{path}/expenses", owner, {"request_id": str(uuid4()), "title": "Test groceries",
                "category": "FOOD", "amount": 100_000, "participants": [owner_id, member_id]})
        debt = request("GET", f"{path}/debts", member)[0]
        ids = {"debt_ids": [debt["id"]]}
        quote = request("POST", f"{path}/debts/qr", member, ids)
        assert quote["amount"] == 50_000 and "amount=50000" in quote["qr_url"]
        request("POST", f"{path}/debts/remind", owner, ids)
        await wait_event(member_ws, "group_debt_reminder")
        ticket = request("POST", f"{path}/proofs/upload", member, {**ids, "size": len(JPEG), "content_type": "image/jpeg"})
        check_storage(ticket)
        proof = request("POST", f"{path}/proofs", member, {**ids, "upload_id": ticket["upload_id"]})
        await wait_event(owner_ws, "group_proof_submitted")
        receipt = f"{path}/proofs/{proof['id']}"
        url = request("GET", receipt, owner)["url"]
        with urlopen(Request(url, headers={"Origin": ORIGIN}), timeout=10) as response:
            with Image.open(BytesIO(response.read())) as receipt_image:
                assert receipt_image.format == "JPEG" and receipt_image.size == (1, 1)
                receipt_image.load()
            assert response.headers.get("Access-Control-Allow-Origin") in {ORIGIN, "*"}
        request("PATCH", f"{receipt}/verify", owner, {"decision": "APPROVE"})
        assert (await wait_event(member_ws, "PROOF_VERIFIED"))["status"] == "PAID"
        assert request("GET", f"{path}/debts", member)[0]["status"] == "PAID"
        assert request("GET", f"{path}/chat", owner)[0]["status"] == "PAID"
    today = today_local()
    request("POST", "/personal-expenses", member, {"type": "EXPENSE", "category": "FOOD",
            "amount": 100_000, "spent_at": today.isoformat()})
    request("PUT", "/budgets", member, {"category": "FOOD", "monthly_limit": 150_000,
            "month": today.replace(day=1).isoformat()})
    summary = request("GET", "/analytics/summary?period=month&scope=combined", member)
    assert summary["personal"] == 100_000 and summary["group"] == 50_000 and summary["trend"]
    advice = request("POST", "/ai-chat/message", member, {"text": "Summarize my spending"})
    assert advice["source"] == "local" and advice["metrics"]["personal"] == 100_000
    assert len(request("GET", "/ai-chat/history", member)) == 1
    print(f"Live smoke check passed. Synthetic group: {group['code']}.")


if __name__ == "__main__":
    asyncio.run(main())
