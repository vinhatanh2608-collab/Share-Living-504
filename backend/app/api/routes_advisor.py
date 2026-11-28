import asyncio
import json
import logging
import re
from uuid import UUID

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import case, func, select, union_all

from .deps import Actor
from .routes_group_finance import lock_group
from .routes_living import approved_member, today_local
from ..core.database import read_query
from ..services.cache_service import cached
from ..models.archive import DebtHistory, GroupHistory, ParticipantHistory, PersonalHistory
from ..models.living import GroupBudgetLimit, LivingGroup, Budget, DebtSplit, GroupExpense, GroupFundTransaction, GroupMember, GroupProof
from ..models.mysql_models import now
from ..services.advisor import provider_reply
from ..services import ai_advisor_service as agent
from ..services.advisor_intent import gemini_intent, local_intent
from ..services.chat_expense import normalize
from ..models.living import AdvisorAction
from ..schemas.advisor import Correction, GroupId, TOOLS, WRITE_TOOLS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analytics and advisor"])
LOCAL = timezone(timedelta(hours=7))


def utc_start(day):
    return datetime.combine(day, time.min, LOCAL).astimezone(timezone.utc).replace(tzinfo=None)


async def summarize(sql, user_id, period, scope, *, include_personal_categories=False, group_ids=None):
    today = today_local()
    month = today.replace(day=1)
    start = {"week": today - timedelta(days=6), "month": month, "year": date(today.year, 1, 1)}[period]
    end = today + timedelta(days=1)
    personal_categories, monthly_spend, trend = defaultdict(int), defaultdict(int), defaultdict(int)
    personal = group = income = 0

    def bucket(day):
        return day.strftime("%Y-%m") if period == "year" else day.isoformat()

    if scope != "group":
        entries = await sql.execute(select(PersonalHistory.type, PersonalHistory.category,
            PersonalHistory.spent_at, func.sum(PersonalHistory.amount)).where(
                PersonalHistory.user_id == user_id, PersonalHistory.spent_at >= start,
                PersonalHistory.spent_at < end).group_by(PersonalHistory.type,
                PersonalHistory.category, PersonalHistory.spent_at))
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
    if group_ids is not None:
        membership = group_ids
    group_window = (GroupHistory.group_id.in_(membership), GroupHistory.created_at >= utc_start(start),
                    GroupHistory.created_at < utc_start(end))
    if scope != "personal":
        historical = ~select(ParticipantHistory.user_id).where(ParticipantHistory.expense_id == GroupHistory.id).exists()
        payer = select(GroupHistory.category, GroupHistory.created_at, GroupHistory.payer_share).where(
            *group_window, historical, GroupHistory.creditor_id == user_id)
        debtor = select(GroupHistory.category, GroupHistory.created_at, DebtHistory.amount).select_from(GroupHistory).join(DebtHistory, DebtHistory.expense_id == GroupHistory.id).where(
            *group_window, historical, DebtHistory.debtor_id == user_id)
        allocated = select(GroupHistory.category, GroupHistory.created_at, ParticipantHistory.share_amount).select_from(GroupHistory).join(
            ParticipantHistory, ParticipantHistory.expense_id == GroupHistory.id).where(*group_window, ParticipantHistory.user_id == user_id, ParticipantHistory.share_amount > 0)
        for category, timestamp, amount in await sql.execute(payer.union_all(debtor, allocated)):
            group += amount
            categories[category] += amount
            trend[bucket(timestamp.replace(tzinfo=timezone.utc).astimezone(LOCAL).date())] += amount

    owing = owed = overdue = 0
    if scope != "personal":
        debtor = DebtSplit.debtor_id == user_id
        cutoff = now() - timedelta(days=3)
        overdue_debt = debtor & (DebtSplit.status == "UNPAID") & (GroupExpense.created_at < cutoff)
        aged = dict((await sql.execute(select(GroupExpense.group_id, func.sum(DebtSplit.amount))
                    .select_from(DebtSplit).join(GroupExpense).where(GroupExpense.group_id.in_(membership),
                    overdue_debt).group_by(GroupExpense.group_id))).all())
        # Approved scopes come from primary. Sum each source by group before computing personal debt totals.
        personal_group = (GroupHistory.group_id.in_(membership),
            ~select(GroupFundTransaction.id).where(GroupFundTransaction.expense_id == GroupHistory.id).exists())
        legacy = ~select(ParticipantHistory.expense_id).where(
            ParticipantHistory.expense_id == GroupHistory.id).exists()
        contributions = select(GroupHistory.group_id, (ParticipantHistory.paid_amount -
            ParticipantHistory.share_amount).label("net")).select_from(GroupHistory).join(
            ParticipantHistory, ParticipantHistory.expense_id == GroupHistory.id).where(
            *personal_group, ParticipantHistory.user_id == user_id)
        legacy_credit = select(GroupHistory.group_id, (GroupHistory.amount - GroupHistory.payer_share).label("net")).where(
            *personal_group, legacy, GroupHistory.creditor_id == user_id)
        legacy_debt = select(GroupHistory.group_id, (-DebtHistory.amount).label("net")).select_from(DebtHistory).join(
            GroupHistory, DebtHistory.expense_id == GroupHistory.id).where(
                *personal_group, legacy, DebtHistory.debtor_id == user_id)
        settled = select(GroupHistory.group_id, case((DebtHistory.debtor_id == user_id, DebtHistory.amount), else_=-DebtHistory.amount).label("net")
            ).select_from(DebtHistory).join(GroupHistory, DebtHistory.expense_id == GroupHistory.id).where(
                *personal_group, DebtHistory.status == "PAID",
                (DebtHistory.debtor_id == user_id) | (GroupHistory.creditor_id == user_id))
        transfers = select(GroupProof.group_id, case((GroupProof.debtor_id == user_id, GroupProof.transfer_amount),
            else_=-GroupProof.transfer_amount).label("net")).where(GroupProof.group_id.in_(membership),
                GroupProof.status == "PAID", GroupProof.transfer_amount.is_not(None),
                (GroupProof.debtor_id == user_id) | (GroupProof.creditor_id == user_id))
        deltas = union_all(contributions, legacy_credit, legacy_debt, settled, transfers).subquery()
        for group_id, total in await sql.execute(select(deltas.c.group_id, func.sum(deltas.c.net)).group_by(deltas.c.group_id)):
            net = int(total)
            owing += max(-net, 0)
            owed += max(net, 0)
            overdue += min(max(-net, 0), int(aged.get(group_id, 0)))

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
            spending_rows = await sql.execute(select(PersonalHistory.category, func.sum(PersonalHistory.amount))
                .where(PersonalHistory.user_id == user_id, PersonalHistory.type == "EXPENSE",
                       PersonalHistory.spent_at >= month, PersonalHistory.spent_at < end)
                .group_by(PersonalHistory.category))
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
    resources = request.app.state.resources
    async with resources.sql() as sql:
        group_ids = sorted(await sql.scalars(select(GroupMember.group_id).where(
            GroupMember.user_id == actor.id, GroupMember.status == "APPROVED"))) if scope != "personal" else []

    async def query(sql):
        return await summarize(sql, actor.id, period, scope, group_ids=group_ids)

    return await cached(request, ["analytics", actor.id, period, scope, today_local().isoformat(), group_ids],
                        lambda: read_query(resources, query))


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


def history_filter(uid, session_id):
    selector = {"user_id": uid}
    if session_id is not None:
        selector["session_id"] = str(session_id)
    return selector


async def load_history(resources, uid, session_id=None, limit=50):
    rows = await resources.mongo.ai_chat_history.find(history_filter(uid, session_id),
        {"_id": 0, "user_id": 0, "created_at": 0}).sort("_id", -1).limit(limit).to_list(limit)
    ids = [row["action"]["id"] for row in rows if row.get("action")]
    protected = any(row.get("group_scopes") for row in rows)
    if ids or protected:
        async with resources.sql() as sql:
            allowed = set(await agent.scopes(sql, uid)) if protected else set()
            actions = await sql.scalars(select(AdvisorAction).where(
                AdvisorAction.user_id == uid, AdvisorAction.id.in_(ids))) if ids else []
            current = {action.id: agent.card(action) for action in actions}
        rows = [row for row in rows if allowed.issuperset(row.get("group_scopes", ()))]

        for row in rows:
            if row.get("action"):
                row["action"] = current.get(row["action"]["id"])
    for row in rows:
        row.pop("group_scopes", None)
    return list(reversed(rows))


@router.get("/ai-chat/history")
async def advisor_history(request: Request, actor: Actor, session_id: UUID | None = None,
                          limit: int = Query(50, ge=1, le=50)):
    return await load_history(request.app.state.resources, actor.id, session_id, limit)


@router.delete("/ai-chat/history", status_code=204)
async def clear_advisor_history(request: Request, actor: Actor, session_id: UUID | None = None):
    await request.app.state.resources.mongo.ai_chat_history.delete_many(history_filter(actor.id, session_id))


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
    await resources.mongo.ai_chat_history.insert_one({"user_id": actor.id, "created_at": now(), **result})
    return result


class GroupLimitIn(BaseModel):
    monthly_limit: int = Field(ge=1, le=10**12, strict=True)


@router.patch("/groups/{group_id}/budget-limit")
async def set_group_limit(group_id: int, data: GroupLimitIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        group = await sql.get(LivingGroup, group_id)
        if group.owner_id != actor.id:
            raise HTTPException(403, "Chỉ chủ nhóm được đặt ngân sách.")
        limit = await sql.get(GroupBudgetLimit, group_id)
        if limit:
            limit.monthly_limit = data.monthly_limit
        else:
            sql.add(GroupBudgetLimit(group_id=group_id, monthly_limit=data.monthly_limit))
    return data


def budget_status(spend, limit):
    if limit and spend >= limit:
        return "exceeded"
    return "warning" if limit and spend * 5 >= limit * 4 else "normal"


@router.get("/groups/{group_id}/advisor/insights")
async def group_insights(group_id: int, request: Request, actor: Actor):
    month = today_local().replace(day=1)
    previous = (month - timedelta(days=1)).replace(day=1)
    following = (month + timedelta(days=32)).replace(day=1)
    resources = request.app.state.resources
    async with resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)

    async def load():
        async with resources.sql() as sql:
            limit = await sql.get(GroupBudgetLimit, group_id)
            current = GroupHistory.created_at >= utc_start(month)
            rows = (await sql.execute(select(current, GroupHistory.category, func.sum(GroupHistory.amount)).where(
                GroupHistory.group_id == group_id, GroupHistory.created_at >= utc_start(previous),
                GroupHistory.created_at < utc_start(following)).group_by(current, GroupHistory.category))).all()
            return {"cap": limit.monthly_limit if limit else None, "rows": [tuple(row) for row in rows]}

    snapshot = await cached(request, ["group-insights", group_id, month.isoformat()], load)
    cap, rows = snapshot["cap"], snapshot["rows"]
    categories = {category: int(total) for newest, category, total in rows if newest}
    spent = sum(categories.values())
    prior = sum(int(total) for newest, _, total in rows if not newest)
    delta = round((spent - prior) * 100 / prior, 1) if prior else None
    top = min(categories, key=lambda key: (-categories[key], key)) if categories else None
    usage = round(spent * 100 / cap, 1) if cap else None
    status = budget_status(spent, cap)
    trend = ("Chưa có chi tiêu tháng trước để so sánh." if delta is None else
             f"So với cả tháng trước, chi tiêu hiện tại {'tăng' if delta > 0 else 'giảm'} {abs(delta):g}%."
             if delta else "Chi tiêu hiện tại bằng cả tháng trước.")
    guidance = {"exceeded": "Nhóm đã chạm hoặc vượt ngân sách tháng.",
                "warning": "Nhóm đã dùng ít nhất 80% ngân sách tháng.",
                "normal": "Nhóm còn trong mức ngân sách." if cap else "Nhóm chưa đặt ngân sách tháng."}
    summary = f"Tháng này nhóm đã chi {spent:,} đồng. {trend} {guidance[status]}".replace(",", ".")
    metrics = {"period": month.strftime("%Y-%m"), "current_month_spend": spent,
               "previous_month_spend": prior, "delta_percent": delta, "monthly_limit": cap,
               "usage_percent": usage, "status": status, "top_category": top}
    state = request.app.state
    if hasattr(state, "http") and hasattr(state, "advisor_breaker"):
        suggestion = await provider_reply(
            "Viết một câu tiếng Việt gợi ý cách theo dõi chi tiêu nhóm. Không đưa số, không đề nghị chuyển tiền.",
            metrics, state.http, state.advisor_breaker, gemini_only=True)
        if suggestion and len(suggestion) <= 180 and not any(char.isdigit() for char in suggestion):
            sentence = suggestion.strip().splitlines()[0].split(".")[0].strip()
            if sentence:
                summary = f"Tháng này nhóm đã chi {spent:,} đồng. {trend} {sentence}.".replace(",", ".")
    return {**metrics, "summary": summary}


class AgentPrompt(Prompt):
    session_id: UUID | None = None
    group_id: GroupId | None = None


FOLLOW_UPS = ["Tháng này tôi đã chi bao nhiêu?", "Còn tháng trước thì sao?", "Nhóm còn khoản nợ nào?"]


async def invalidate_advisor_cache(request):
    cache = getattr(request.app.state, "cache", None)
    if cache:
        await cache.invalidate()


@router.post("/ai-chat/actions/{action_id}/confirm")
async def confirm_advisor_action(action_id: UUID, request: Request, actor: Actor):
    result = await agent.apply_action(request.app.state.resources, actor.id, str(action_id))
    await invalidate_advisor_cache(request)
    return result


@router.post("/ai-chat/actions/{action_id}/undo")
async def undo_advisor_action(action_id: UUID, request: Request, actor: Actor):
    result = await agent.undo_action(request.app.state.resources, actor.id, str(action_id))
    await invalidate_advisor_cache(request)
    return result


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_agent(request, uid, data, history):
    state = request.app.state
    local = local_intent(data.text, today_local(), history, data.group_id)
    external = None
    if data.use_external and (not local or local[0] not in {"correct", "guide", "undo_last_ai_action"}):
        # Share only the typed prompt, date, selected group ID and previous user prompts after opt-in.
        external = await gemini_intent(state, data.text, {"today": today_local().isoformat(),
            "selected_group": data.group_id, "previous_prompts": [row["prompt"][:350] for row in history[-4:]]})
    action, visual = None, {}
    if not external and not local:
        return ("Hãy thử hỏi về chi tiêu, ngân sách, công nợ hoặc nhập “ăn sáng 35k, đổ xăng 50k”. "
                "Khi ghi khoản chi nhóm, hãy chọn nhóm và nêu cách chia.", action, visual, "local")
    name, raw = external or local
    if name == "guide" and not external:
        return raw, None, {}, "local"
    schema = Correction if name == "correct" and not external else TOOLS.get(name)
    try:
        if schema is None:
            raise ValueError("Unknown tool")
        args = schema.model_validate(raw)
    except (ValidationError, ValueError):
        if external and local:
            name, raw = local
            try:
                args = (Correction if name == "correct" else TOOLS[name]).model_validate(raw)
            except ValidationError:
                return "Thông tin chưa hợp lệ. Hãy kiểm tra số tiền hoặc ngày.", None, {}, "local"
            external = None
        else:
            return "Thông tin chưa hợp lệ. Hãy kiểm tra số tiền hoặc ngày.", None, {}, "local"
    if name in WRITE_TOOLS:
        if name == "create_group_expense_draft_or_record" and args.split_type == "clarify":
            return "Bạn muốn chia đều cho tất cả thành viên hay chia theo phần riêng? Hãy ghi rõ cách chia.", None, {}, "local"
        action = await agent.stage(state.resources, uid, name, args)
        reply = "Kiểm tra thông tin trên thẻ rồi bấm Xác nhận để lưu."
    elif name == "undo_last_ai_action":
        if external:
            return "Chọn Hoàn tác trên thẻ của khoản bạn muốn đổi.", None, {}, "provider"
        action = await agent.undo_action(state.resources, uid, args.action_id)
        await invalidate_advisor_cache(request)
        reply = "Đã hoàn tác."
    elif name == "correct":
        action = await agent.correct_action(state.resources, uid, args)
        await invalidate_advisor_cache(request)
        reply = "Đã sửa số tiền của khoản chi vừa ghi."
    else:
        group_scope = None
        if name == "get_spending_analytics":
            normalized = normalize(data.text)
            if "nhom" in normalized.split():
                if not data.group_id:
                    return "Hãy chọn một nhóm đã được duyệt để xem chi tiêu nhóm.", None, {}, "local"
                group_scope = data.group_id
            elif "thang truoc" in normalized and history:
                prior = (history[-1].get("visual") or {}).get("budget") or {}
                group_scope = prior.get("group_id")
                if args.category is None and prior.get("category"):
                    args = args.model_copy(update={"category": prior["category"]})
        reply, visual = await agent.read_tool(request, uid, name, args, group_scope)
    return reply, action, visual, "provider" if external else "local"


@router.post("/ai-chat/stream")
async def advisor_stream(data: AgentPrompt, request: Request, actor: Actor):
    resources = request.app.state.resources
    if data.group_id is not None:
        async with resources.sql() as sql:
            await approved_member(sql, data.group_id, actor.id)
    history = await load_history(resources, actor.id, data.session_id, limit=8)

    async def stream():
        yield event("status", {"text": "Đang phân tích yêu cầu và kiểm tra dữ liệu…"})
        try:
            reply, action, visual, source = await run_agent(request, actor.id, data, history)
            group_scopes = visual.pop("group_scopes", [])
            if action and action["tool"] == "create_group_expense_draft_or_record":
                group_scopes.append(action["details"]["group_id"])
            turn = {"prompt": data.text, "reply": reply, "quick_actions": [], "source": source,
                    "metrics": {"personal": 0, "group": 0, "overdue": 0}, "action": action,
                    "visual": visual, "suggestions": FOLLOW_UPS}
            await resources.mongo.ai_chat_history.insert_one({"user_id": actor.id, "created_at": now(),
                "session_id": str(data.session_id) if data.session_id else None, "group_scopes": group_scopes, **turn})
        except HTTPException as exc:
            yield event("error", {"text": "Bạn không có quyền truy cập dữ liệu này." if exc.status_code in {403, 404}
                        else exc.detail})
            return
        except Exception:
            logger.exception("Advisor stream failed")
            yield event("error", {"text": "Trợ lý đang bận. Vui lòng thử lại."})
            return
        for match in re.finditer(r".{1,60}", reply, flags=re.S):
            yield event("text", {"text": match[0]})
            await asyncio.sleep(0)
        if action:
            if action["status"] == "applied":
                yield event("status", {"text": "Đã cập nhật sổ chi tiêu."})
            elif action["status"] == "proposed":
                yield event("status", {"text": "Đã chuẩn bị bản nháp để bạn xem lại."})
            yield event("action", action)
        yield event("suggestions", FOLLOW_UPS)
        yield event("done", turn)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
