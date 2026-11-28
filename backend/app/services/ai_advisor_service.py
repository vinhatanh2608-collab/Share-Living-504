"""Scoped advisor queries and transactional actions."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from ..api.routes_group_finance import lock_group, save_expense
from ..api.routes_living import approved_member, today_local
from ..models.archive import GroupHistory, PersonalHistory
from ..models.living import (AdvisorAction, AdvisorOverallBudget, Budget, DebtSplit, ExpenseParticipant,
                             GroupBudgetLimit, GroupExpense, GroupMember, GroupProof, LivingGroup, PersonalTransaction)
from ..models.mysql_models import User
from ..schemas.advisor import TOOLS
from ..schemas.living import GroupExpenseIn
from .cache_service import cached
from .debt_simplifier import dong, simplify_group

LOCAL = timezone(timedelta(hours=7))
CATEGORIES = {"FOOD": "Ăn uống", "TRANSPORT": "Đi lại", "HOUSING": "Nhà ở",
              "STUDY": "Học tập", "ENTERTAINMENT": "Giải trí", "OTHER": "Khác", "OVERALL": "Tổng chi"}


def month_start(value):
    return date.fromisoformat(value + "-01") if value else today_local().replace(day=1)


def card(action):
    details = dict(action.payload)
    if action.tool == "create_group_expense_draft_or_record":
        details["members"] = (action.changes or {}).get("members", [])
    return {"id": action.id, "tool": action.tool, "status": action.status, "details": details}


async def scopes(sql, uid):
    return list(await sql.scalars(select(GroupMember.group_id).where(
        GroupMember.user_id == uid, GroupMember.status == "APPROVED").order_by(GroupMember.group_id)))


async def analytics(sql, uid, args, group_id=None):
    start = month_start(args.month)
    if start.year == 9999:
        raise HTTPException(422, "Tháng nằm ngoài phạm vi hỗ trợ.")
    prior = (start - timedelta(days=1)).replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    if group_id is None:
        model = PersonalHistory
        day = model.spent_at
        first, last, threshold = prior, end, start
        terms = [model.user_id == uid, model.type == "EXPENSE"]
        budget = await (sql.get(Budget, (uid, start, args.category)) if args.category
                        else sql.get(AdvisorOverallBudget, (uid, start)))
    else:
        model = GroupHistory
        day = model.created_at
        first, last, threshold = [datetime.combine(value, time.min, LOCAL).astimezone(
            timezone.utc).replace(tzinfo=None) for value in (prior, end, start)]
        terms = [model.group_id == group_id]
        budget = await sql.get(GroupBudgetLimit, group_id) if not args.category and start == today_local().replace(day=1) else None
    current = day >= threshold
    query = select(current, func.sum(model.amount)).where(*terms, day >= first, day < last)
    if args.category:
        query = query.where(model.category == args.category)
    totals = dict((await sql.execute(query.group_by(current))).all())
    spent, previous = int(totals.get(True, 0)), int(totals.get(False, 0))
    cap = budget.monthly_limit if budget else None
    today = today_local()
    days = today.day if start <= today < end else (end - start).days
    return {"month": start.strftime("%Y-%m"), "spent": spent, "previous": previous,
            "category": args.category, "limit": cap, "usage_percent": round(spent * 100 / cap, 1) if cap else None,
            "daily": round(spent / days), "delta_percent": round((spent-previous)*100/previous, 1) if previous else None}


async def search(sql, uid, args):
    groups = await scopes(sql, uid)
    personal = select(PersonalHistory.note.label("title"), PersonalHistory.amount, PersonalHistory.category,
                      PersonalHistory.spent_at.label("day")).where(
        PersonalHistory.user_id == uid, PersonalHistory.type == "EXPENSE")
    shared = select(GroupHistory.title, GroupHistory.amount, GroupHistory.category,
                    func.date(GroupHistory.created_at).label("day")).where(GroupHistory.group_id.in_(groups))
    if args.category:
        personal = personal.where(PersonalHistory.category == args.category)
        shared = shared.where(GroupHistory.category == args.category)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip()
        personal = personal.where(PersonalHistory.note.startswith(keyword, autoescape=True))
        shared = shared.where(GroupHistory.title.startswith(keyword, autoescape=True))
    records = personal.union_all(shared).subquery()
    result = await sql.execute(select(records).order_by(records.c.day.desc(), records.c.title).limit(args.limit))
    return groups, [{"title": row.title, "amount": row.amount, "category": row.category, "day": str(row.day)} for row in result]


async def debts(sql, uid, args):
    if args.group_id is not None:
        await approved_member(sql, args.group_id, uid)
        group_ids = [args.group_id]
    else:
        group_ids = (await scopes(sql, uid))[:10]
    result = []
    for group_id in group_ids:
        group = await sql.get(LivingGroup, group_id)
        data = await simplify_group(sql, group_id)
        names = {row["user_id"]: row["name"] for row in data["member_breakdown"]}
        def transfer(row):
            return {"debtor": names.get(row["debtor_id"], "Thành viên"),
                    "creditor": names.get(row["creditor_id"], "Thành viên"), "amount": row["amount"]}
        result.append({"group_id": group_id, "name": group.name, "transfer_count": len(data["transfers"]),
                       "pending_count": len(data["pending"]),
                       "transfers": [transfer(row) for row in data["transfers"][:20]],
                       "pending": [transfer(row) for row in data["pending"][:20]]})
    return result


async def stage(resources, uid, tool, args):
    async with resources.sql.begin() as sql:
        preview = None
        if tool == "create_group_expense_draft_or_record":
            await approved_member(sql, args.group_id, uid)
            members = (await sql.execute(select(User.id, User.name).join(GroupMember,
                GroupMember.user_id == User.id).where(GroupMember.group_id == args.group_id,
                GroupMember.status == "APPROVED").order_by(User.id))).all()
            if not members or len(members) > 25 or args.amount < len(members):
                raise HTTPException(422, "Không thể chia đều khoản chi này cho tất cả thành viên.")
            each, remainder = divmod(args.amount, len(members))
            preview = {"members": [{"id": row.id, "name": row.name, "amount": each + int(index < remainder)}
                                   for index, row in enumerate(members)]}
        action = AdvisorAction(id=str(uuid4()), user_id=uid, tool=tool,
            payload=args.model_dump(mode="json"), changes=preview, status="proposed")
        sql.add(action)
        return card(action)


async def owned_action(sql, uid, ident):
    await sql.get(User, uid, with_for_update=True)
    action = await sql.scalar(select(AdvisorAction).where(
        AdvisorAction.id == ident, AdvisorAction.user_id == uid).with_for_update())
    if not action:
        raise HTTPException(404, "Không tìm thấy thao tác.")
    return action


async def budget_row(sql, uid, args):
    month = month_start(args.month)
    model = AdvisorOverallBudget if args.category == "OVERALL" else Budget
    key = (uid, month) if model is AdvisorOverallBudget else (uid, month, args.category)
    return model, month, await sql.get(model, key, with_for_update=True)


async def apply_action(resources, uid, ident):
    async with resources.sql.begin() as sql:
        action = await owned_action(sql, uid, ident)
        if action.status == "applied":
            return card(action)
        if action.status != "proposed":
            raise HTTPException(409, "Thao tác đã được xử lý.")
        args = TOOLS[action.tool].model_validate(action.payload)
        if action.tool == "record_personal_expenses":
            items = [PersonalTransaction(user_id=uid, type="EXPENSE", **item.model_dump()) for item in args.items]
            sql.add_all(items)
            await sql.flush()
            action.changes = {"ids": [item.id for item in items]}
        elif action.tool == "upsert_monthly_budget":
            model, month, row = await budget_row(sql, uid, args)
            action.changes = {"previous": row.monthly_limit if row else None}
            action.payload = {**action.payload, "month": month.strftime("%Y-%m")}
            if row:
                row.monthly_limit = args.limit_amount
            else:
                extra = {"category": args.category} if model is Budget else {}
                sql.add(model(user_id=uid, month=month, monthly_limit=args.limit_amount, **extra))
        else:
            await lock_group(sql, args.group_id, uid)
            members = await scopes_for_split(sql, args.group_id)
            if args.split_type != "equal" or not members or len(members) > 25 or args.amount < len(members):
                raise HTTPException(422, "Không thể chia đều khoản chi này cho tất cả thành viên.")
            if members != [row["id"] for row in action.changes["members"]]:
                raise HTTPException(409, "Danh sách nhóm đã thay đổi. Hãy tạo bản nháp mới.")
            data = GroupExpenseIn(request_id=action.id, title=args.description, category="OTHER",
                                  amount=args.amount, participants=members)
            result = await save_expense(sql, args.group_id, uid, data)
            action.changes = {**action.changes, "expense_id": result["id"]}
        action.status = "applied"
        return card(action)


async def scopes_for_split(sql, group_id):
    return list(await sql.scalars(select(GroupMember.user_id).where(
        GroupMember.group_id == group_id, GroupMember.status == "APPROVED").order_by(GroupMember.user_id)))


async def unchanged_personal(sql, uid, action, args):
    ids = action.changes["ids"]
    rows = {row.id: row for row in await sql.scalars(select(PersonalTransaction).where(
        PersonalTransaction.user_id == uid, PersonalTransaction.id.in_(ids)).with_for_update())}
    if any(ident not in rows or rows[ident].type != "EXPENSE" or any(
        getattr(rows[ident], key) != value for key, value in item.model_dump().items()
    ) for ident, item in zip(ids, args.items)):
        raise HTTPException(409, "Khoản chi đã thay đổi. Hãy kiểm tra sổ chi tiêu.")
    return [rows[ident] for ident in ids]


async def undo_action(resources, uid, ident):
    async with resources.sql.begin() as sql:
        action = await owned_action(sql, uid, ident)
        if action.status == "undone":
            return card(action)
        if action.status != "applied":
            raise HTTPException(409, "Chưa có thay đổi để hoàn tác.")
        args = TOOLS[action.tool].model_validate(action.payload)
        if action.tool == "record_personal_expenses":
            for row in await unchanged_personal(sql, uid, action, args):
                await sql.delete(row)
        elif action.tool == "upsert_monthly_budget":
            _, _, row = await budget_row(sql, uid, args)
            if row is None or row.monthly_limit != args.limit_amount:
                raise HTTPException(409, "Ngân sách đã thay đổi.")
            if action.changes["previous"] is None:
                await sql.delete(row)
            else:
                row.monthly_limit = action.changes["previous"]
        else:
            await lock_group(sql, args.group_id, uid)
            expense = await sql.get(GroupExpense, action.changes["expense_id"], with_for_update=True)
            if (expense is None or expense.creditor_id != uid or expense.group_id != args.group_id or
                expense.title != args.description or expense.amount != args.amount or expense.category != "OTHER"
                or await sql.scalar(select(GroupProof.id).where(GroupProof.group_id == args.group_id,
                    GroupProof.transfer_amount.is_not(None), GroupProof.status.in_(("PAID", "PENDING"))).limit(1))):
                raise HTTPException(409, "Khoản chi nhóm đã thay đổi hoặc nhóm có biên lai.")
            splits = list(await sql.scalars(select(DebtSplit).where(DebtSplit.expense_id == expense.id)))
            participants = list(await sql.scalars(select(ExpenseParticipant).where(
                ExpenseParticipant.expense_id == expense.id).order_by(ExpenseParticipant.user_id)))
            shares = divmod(expense.amount, len(participants)) if participants else (0, 0)
            if not participants or any(row.status != "UNPAID" or row.proof_id for row in splits) or any(
                row.share_amount != shares[0] + (index < shares[1])
                or row.paid_amount != (expense.amount if row.user_id == uid else 0)
                for index, row in enumerate(participants)
            ):
                raise HTTPException(409, "Cách chia hoặc trạng thái thanh toán đã thay đổi.")
            await sql.execute(delete(DebtSplit).where(DebtSplit.expense_id == expense.id))
            await sql.execute(delete(ExpenseParticipant).where(ExpenseParticipant.expense_id == expense.id))
            await sql.delete(expense)
        action.status = "undone"
        return card(action)


async def correct_action(resources, uid, args):
    async with resources.sql.begin() as sql:
        action = await owned_action(sql, uid, args.action_id)
        if action.status != "applied" or action.tool != "record_personal_expenses":
            raise HTTPException(409, "Chỉ sửa được khoản chi cá nhân đã ghi.")
        old = TOOLS[action.tool].model_validate(action.payload)
        rows = await unchanged_personal(sql, uid, action, old)
        if len(rows) != 1:
            raise HTTPException(409, "Lần ghi trước có nhiều khoản. Hãy sửa từng khoản trong sổ chi tiêu.")
        rows[0].amount = args.amount
        action.payload = {"items": [{**action.payload["items"][0], "amount": args.amount}]}
        return card(action)


async def read_tool(request, uid, tool, args, group_id=None):
    resources = request.app.state.resources
    if tool == "get_spending_analytics":
        if group_id is not None:
            async with resources.sql() as sql:
                await approved_member(sql, group_id, uid)

        async def load():
            async with resources.sql() as sql:
                return await analytics(sql, uid, args, group_id)
        data = await cached(request, ["advisor-context", uid, group_id, today_local().isoformat(),
                                      args.model_dump(mode="json")], load, ttl=45)
        label = CATEGORIES.get(data["category"], "Chi cả nhóm" if group_id else "Chi cá nhân")
        reply = (f"{label} tháng {data['month']}: {dong(data['spent'])}. "
                 f"Tháng trước: {dong(data['previous'])}. Bình quân: {dong(data['daily'])}/ngày.")
        if data["limit"] is not None:
            reply += f" Đã dùng {data['usage_percent']}% hạn mức."
        return reply, {"budget": {**data, "group_id": group_id}, "group_scopes": [group_id] if group_id else []}
    async with resources.sql() as sql:
        if tool == "search_transactions":
            groups, rows = await search(sql, uid, args)
            return ("\n".join(f"{row['day']} · {row['title']} · {dong(row['amount'])}" for row in rows)
                    if rows else "Không tìm thấy khoản chi phù hợp."), {"group_scopes": groups}
        groups = await debts(sql, uid, args)
    lines = [f"{group['name']}: {group['transfer_count']} lượt chuyển khoản, {group['pending_count']} biên lai chờ duyệt."
             for group in groups]
    for group in groups:
        lines.extend(f"{row['debtor']} → {row['creditor']}: {dong(row['amount'])}" for row in group["transfers"])
    return "\n".join(lines) if lines else "Bạn chưa có nhóm được duyệt.", {"debts": groups, "group_scopes": [group["group_id"] for group in groups]}
