import argparse
import asyncio
import os
from datetime import timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID
from sqlalchemy import select
from app.core.config import settings
from app.core.database import Resources
from app.core.security import passwords
from app.models.mysql_models import User, now
from app.models.living import Budget, DebtSplit, GroupExpense, GroupMember, LivingGroup, PersonalTransaction
from app.api.routes_advisor import today_local
from app.schemas.finance import ExpenseIn
from app.services.finance_service import create_expense
from app.services.storage_service import client


async def seed_living():
    resources = Resources()
    credentials = []
    try:
        await resources.initialize_schema()
        async with resources.sql.begin() as sql:
            prior = await sql.scalar(select(LivingGroup).where(LivingGroup.code == "GRP-DEMO01"))
            if prior:
                print("GRP-DEMO01 already exists.")
                return
            users = []
            for number in range(1, 4):
                username = f"living_demo_{number}"
                password = token_urlsafe(24)
                user = User(username=username, name=f"Demo roommate {number}",
                            password_hash=await asyncio.to_thread(passwords.hash, password))
                sql.add(user)
                users.append(user)
                credentials.append((username, password))
            await sql.flush()
            group = LivingGroup(code="GRP-DEMO01", name="Demo household", owner_id=users[0].id)
            sql.add(group)
            await sql.flush()
            today = today_local()
            for number, user in enumerate(users, start=1):
                sql.add(GroupMember(group_id=group.id, user_id=user.id, status="APPROVED",
                                    bank_bin="970436", bank_account_no=f"000000000{number}",
                                    bank_account_name=f"DEMO ONLY {number}"))
                sql.add(PersonalTransaction(user_id=user.id, type="EXPENSE", category="FOOD",
                                            amount=50_000, note="Demo groceries", spent_at=today))
                sql.add(Budget(user_id=user.id, month=today.replace(day=1), category="FOOD", monthly_limit=400_000))
            expense = GroupExpense(group_id=group.id, creditor_id=users[0].id, title="Demo utility bill",
                                   category="HOUSING", amount=240_000, payer_share=80_000,
                                   request_id="fef7f199-f5ae-4d2c-9f67-d2259538bdc9", request_hash="demo")
            sql.add(expense)
            await sql.flush()
            sql.add_all(DebtSplit(expense_id=expense.id, debtor_id=user.id, amount=80_000) for user in users[1:])
        print("Demo group GRP-DEMO01 uses synthetic bank details.")
        for username, password in credentials:
            print(f"{username}: {password}")
    finally:
        await resources.close()


async def main(demo):
    cfg = settings()
    if demo and cfg.environment != "development":
        raise SystemExit("Demo fixtures are development-only")
    if not cfg.legacy_room:
        if not demo:
            raise SystemExit("Create Shared Living accounts in the app. Use --demo for development fixtures.")
        await seed_living()
        return
    owner_password = os.environ.get("OWNER_PASSWORD", "")
    if demo and not owner_password:
        owner_password = token_urlsafe(24)
    if not 12 <= len(owner_password.encode()) <= 72 or "\0" in owner_password:
        raise SystemExit("Set OWNER_PASSWORD to 12–72 UTF-8 bytes")
    resources = Resources()
    try:
        await resources.initialize_schema()
        new_credentials = []
        async with resources.sql.begin() as sql:
            users = []
            for i in range(1, 9):
                username = cfg.owner_username if i == 1 else f"roommate{i}"
                if i > 1 and username == cfg.owner_username:
                    raise SystemExit("OWNER_USERNAME conflicts with a roommate username")
                user = await sql.scalar(select(User).where(User.username == username))
                if not user:
                    password = owner_password if i == 1 else token_urlsafe(24)
                    user = User(username=username, name=f"Roommate {i}",
                                password_hash=await asyncio.to_thread(passwords.hash, password))
                    if demo:
                        user.bank_bin = "970436"
                        user.account_number = f"000000000{i}"
                        user.account_name = f"DEMO ROOMMATE {i}"
                    sql.add(user)
                    new_credentials.append((username, password))
                users.append(user)
            await sql.flush()
        for username, password in new_credentials:
            if demo or username != cfg.owner_username:
                print(f"{username}: {password}")
        if demo:
            # Keep the same due date on retries, so the expense request remains idempotent.
            owner = users[0]
            rid = UUID("8bf73bef-b139-4530-b22e-9c6bb26a67fc")
            from app.models.mysql_models import Expense
            async with resources.sql() as sql:
                existing = await sql.scalar(select(Expense).where(
                    Expense.creditor_id == owner.id, Expense.request_id == str(rid)))
            if not existing:
                await create_expense(resources, owner.id, ExpenseIn(
                    request_id=rid, title="Demo utility bill", amount=240000,
                    participants=[u.id for u in users],
                    due_at=(now() + timedelta(days=7)).replace(tzinfo=timezone.utc)))
            await resources.mongo.notes.update_one({"_id": "room-study-plan"},
                {"$setOnInsert": {"text": "Add study topics for the week.", "version": 1,
                                  "editor_id": owner.id, "updated_at": now()}}, upsert=True)
            await resources.mongo.messages.update_one({"_id": "demo-welcome"},
                {"$setOnInsert": {"kind": "text", "sender_id": owner.id, "text": "Welcome!",
                                  "sort_key": "000000000000000000000000", "created_at": now()}}, upsert=True)
            ident = f"{owner.id}:demo-study-notes"
            key = "objects/demo/study-notes.txt"
            if not await resources.mongo.materials.find_one({"_id": ident}):
                await asyncio.to_thread(lambda: client().put_object(
                    Bucket=cfg.s3_bucket, Key=key, Body=b"Study plan: Python, SQL, and Git.\n",
                    ContentType="application/octet-stream", ContentDisposition="attachment"))
                await resources.mongo.materials.insert_one({
                    "_id": ident, "owner_id": owner.id, "key": key, "created_at": now(),
                    "title": "Demo study notes", "description": "Python, SQL, and Git"})
    finally:
        await resources.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    asyncio.run(main(parser.parse_args().demo))
