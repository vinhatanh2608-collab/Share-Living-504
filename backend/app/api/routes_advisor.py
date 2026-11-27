from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from .deps import Actor
from ..models.living import Budget, DebtSplit, GroupExpense, GroupMember, PersonalTransaction
from ..models.mysql_models import now
from ..services.advisor import provider_reply

router = APIRouter(tags=["analytics and advisor"])
LOCAL = timezone(timedelta(hours=7))


def today_local():
    return datetime.now(LOCAL).date()


def utc_start(day):
    return datetime.combine(day, time.min, LOCAL).astimezone(timezone.utc).replace(tzinfo=None)


async def summarize(sql, user_id, period, scope, *, include_personal_categories=False):
    today = today_local()
    month = today.replace(day=1)
    start = {"week": today - timedelta(days=6), "month": month, "year": date(today.year, 1, 1)}[period]
    end = today + timedelta(days=1)
    personal_categories, monthly_spend, trend = defaultdict(int), defaultdict(int), defaultdict(int)
    personal = group = income = 0

    def bucket(day):
        return day.strftime("%Y-%m") if period == "year" else day.isoformat()

    if scope != "group":
        entries = await sql.execute(select(PersonalTransaction.type, PersonalTransaction.category,
            PersonalTransaction.spent_at, func.sum(PersonalTransaction.amount)).where(
                PersonalTransaction.user_id == user_id, PersonalTransaction.spent_at >= start,
                PersonalTransaction.spent_at < end).group_by(PersonalTransaction.type,
                PersonalTransaction.category, PersonalTransaction.spent_at))
        for kind, category, day, amount in entries:
            if kind == "INCOME":
                income += int(amount)
            else:
                personal += int(amount)
                personal_categories[category] += int(amount)
                if day >= month:
                    monthly_spend[category] += int(amount)
                trend[bucket(day)] += int(amount)

    categories = defaultdict(int, personal_categories)
    membership = select(GroupMember.group_id).where(GroupMember.user_id == user_id, GroupMember.status == "APPROVED")
    group_window = (GroupExpense.group_id.in_(membership), GroupExpense.created_at >= utc_start(start),
                    GroupExpense.created_at < utc_start(end))
    if scope != "personal":
        payer = select(GroupExpense.category, GroupExpense.created_at, GroupExpense.payer_share).where(
            *group_window, GroupExpense.creditor_id == user_id)
        debtor = select(GroupExpense.category, GroupExpense.created_at, DebtSplit.amount).join(DebtSplit).where(
            *group_window, DebtSplit.debtor_id == user_id)
        for category, timestamp, amount in await sql.execute(payer.union_all(debtor)):
            group += amount
            categories[category] += amount
            trend[bucket(timestamp.replace(tzinfo=timezone.utc).astimezone(LOCAL).date())] += amount

    owing = owed = overdue = 0
    if scope != "personal":
        debtor = DebtSplit.debtor_id == user_id
        cutoff = now() - timedelta(days=3)
        overdue_debt = debtor & (DebtSplit.status == "UNPAID") & (GroupExpense.created_at < cutoff)
        balances = await sql.execute(select(
            func.sum(case((debtor, DebtSplit.amount), else_=0)),
            func.sum(case((debtor, 0), else_=DebtSplit.amount)),
            func.sum(case((overdue_debt, DebtSplit.amount), else_=0)),
        ).select_from(DebtSplit).join(GroupExpense).where(
            GroupExpense.group_id.in_(membership), DebtSplit.status.in_(["UNPAID", "PENDING"]),
            debtor | (GroupExpense.creditor_id == user_id)))
        owing, owed, overdue = (int(value or 0) for value in balances.one())

    budgets = []
    monthly_limits = defaultdict(int)
    if scope != "group":
        budget_rows = await sql.scalars(select(Budget).where(
            Budget.user_id == user_id, Budget.month >= start.replace(day=1), Budget.month <= month))
        for row in budget_rows:
            monthly_limits[row.month] += row.monthly_limit
            if row.month == month:
                budgets.append(row)
        if budgets and start > month:
            spending_rows = await sql.execute(select(PersonalTransaction.category, func.sum(PersonalTransaction.amount))
                .where(PersonalTransaction.user_id == user_id, PersonalTransaction.type == "EXPENSE",
                       PersonalTransaction.spent_at >= month, PersonalTransaction.spent_at < end)
                .group_by(PersonalTransaction.category))
            monthly_spend = dict(spending_rows.all())
    budget_trend = {}
    for offset in range((end - start).days):
        day = start + timedelta(days=offset)
        key = bucket(day)
        trend.setdefault(key, 0)
        if key not in budget_trend:
            first = day.replace(day=1)
            days = ((first + timedelta(days=32)).replace(day=1) - first).days
            limit = monthly_limits[first]
            budget_trend[key] = limit if period == "year" else round(limit / days)
    total = personal + group
    result = {"categories": [{"category": key, "amount": value, "percentage": round(value * 100 / total, 1)}
                            for key, value in sorted(categories.items())] if total else [],
            "trend": [{"date": key, "spending": value, "budget": budget_trend[key]} for key, value in sorted(trend.items())],
            "personal": personal, "group": group, "income": income, "owed": owed, "owing": owing, "overdue": overdue,
            "budgets": [{"category": row.category, "monthly_limit": row.monthly_limit,
                         "spent": int(monthly_spend.get(row.category, 0))} for row in budgets], "month": month.isoformat()}

    if include_personal_categories:
        result["personal_categories"] = personal_categories
    return result


@router.get("/analytics/summary")
async def summary(request: Request, actor: Actor, period: Literal["week", "month", "year"] = "month",
                  scope: Literal["personal", "group", "combined"] = "combined"):
    async with request.app.state.resources.sql() as sql:
        return await summarize(sql, actor.id, period, scope)


class Prompt(BaseModel):
    text: str = Field(min_length=1, max_length=1500, pattern=r"\S")
    use_external: bool = Field(default=False, strict=True)


def local_reply(snapshot, personal):
    advice = [f"This month: {snapshot['personal']:,} VND in personal expenses and {snapshot['group']:,} VND in group shares.",
              f"Outstanding balances: {snapshot['owing']:,} VND to pay, {snapshot['owed']:,} VND to receive. "
              f"Your overdue unpaid balance is {snapshot['overdue']:,} VND."]
    day = today_local().day
    for budget in snapshot["budgets"]:
        percentage = budget["spent"] * 100 // budget["monthly_limit"]
        advice.append(f"On day {day}, {budget['category'].lower()} is at {percentage}% of its monthly limit.")
    existing = {budget["category"] for budget in snapshot["budgets"]}
    unbudgeted = {category: personal[category] for category in sorted(personal) if category not in existing}
    actions = []
    if unbudgeted:
        top = max(unbudgeted, key=unbudgeted.get)
        days = ((today_local().replace(day=1) + timedelta(days=32)).replace(day=1) - today_local().replace(day=1)).days
        estimate = min(10**12, max(10_000, (unbudgeted[top] * days // max(day, 7) + 9999) // 10000 * 10000))
        advice.append(f"Review the suggested {top.lower()} limit against your remaining expenses.")
        actions.append({"type": "apply_budget", "category": top, "monthly_limit": estimate, "month": snapshot["month"]})
    return "\n".join(advice), actions


@router.get("/ai-chat/history")
async def advisor_history(request: Request, actor: Actor):
    collection = request.app.state.resources.mongo.ai_chat_history
    rows = await collection.find({"user_id": actor.id}, {"_id": 0, "user_id": 0}).sort("_id", -1).limit(50).to_list(50)
    return list(reversed(rows))


@router.post("/ai-chat/message")
async def advisor_message(data: Prompt, request: Request, actor: Actor):
    resources = request.app.state.resources
    async with resources.sql() as sql:
        snapshot = await summarize(sql, actor.id, "month", "combined", include_personal_categories=True)
    personal = snapshot.pop("personal_categories")
    text, actions = local_reply(snapshot, personal)
    external = await provider_reply(data.text, snapshot, request.app.state.http, request.app.state.advisor_breaker) if data.use_external else None
    result = {"prompt": data.text, "reply": external or text, "quick_actions": actions,
              "source": "provider" if external else "local",
              "metrics": {key: snapshot[key] for key in ("personal", "group", "overdue")}}
    await resources.mongo.ai_chat_history.insert_one({"user_id": actor.id, **result})
    return result
