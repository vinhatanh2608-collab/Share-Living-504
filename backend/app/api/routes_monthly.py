import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from typing import Annotated, Literal

from sqlalchemy import select

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, StrictBool

from .deps import Actor
from .routes_living import approved_member
from .routes_group_finance import fund_balance
from ..models.living import GroupExpense, GroupFundTransaction, LivingGroup
from ..models.mysql_models import User
from ..services.debt_simplifier import simplify_group
from ..services.monthly_reminders import LOCAL, project_monthly, queue_month

router = APIRouter(prefix="/groups/{group_id}", tags=["group-reminders"])


class ReminderSettings(BaseModel):
    auto_monthly_reminder: StrictBool


async def owner_group(sql, group_id, actor_id):
    group = await sql.get(LivingGroup, group_id, with_for_update=True)
    await approved_member(sql, group_id, actor_id, lock=True)
    if not group or group.owner_id != actor_id:
        raise HTTPException(403, "Group owner approval required")
    return group


@router.patch("/reminder-settings")
async def reminder_settings(group_id: int, data: ReminderSettings, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        group = await owner_group(sql, group_id, actor.id)
        group.auto_monthly_reminder = data.auto_monthly_reminder
        return {"auto_monthly_reminder": group.auto_monthly_reminder, "reminder_hour": group.reminder_hour,
                "last_reminded_period": group.last_reminded_period}


@router.post("/reminders/run-monthly")
async def run_monthly(group_id: int, request: Request, actor: Actor):
    resources = request.app.state.resources
    async with resources.sql.begin() as sql:
        group = await owner_group(sql, group_id, actor.id)
        result = await queue_month(sql, group, datetime.now(LOCAL), manual=True)
    if result["status"] == "sent":
        await project_monthly(resources, request.app.state.ws, (group_id, result["period"]))
    return result

@router.get("/monthly-report/export")
async def export_report(group_id: int, request: Request, actor: Actor,
                        period: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")],
                        format: Literal["xlsx", "csv"] = "xlsx"):
    try:
        start = datetime.strptime(period, "%Y-%m").replace(tzinfo=LOCAL)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    except (ValueError, OverflowError):
        raise HTTPException(422, "Tháng báo cáo không hợp lệ.") from None
    first, cutoff = [value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start, end)]
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        rows = (await sql.execute(select(GroupExpense, User.name, GroupFundTransaction.id)
            .join(User, User.id == GroupExpense.creditor_id)
            .outerjoin(GroupFundTransaction, GroupFundTransaction.expense_id == GroupExpense.id)
            .where(GroupExpense.group_id == group_id, GroupExpense.created_at >= first,
                   GroupExpense.created_at < cutoff).order_by(GroupExpense.created_at, GroupExpense.id))).all()
        balance = await fund_balance(sql, group_id, cutoff)
        settlement = await simplify_group(sql, group_id, period_bounds=(first, cutoff))
    names = {row["user_id"]: row["name"] for row in settlement["member_breakdown"]}
    transfers = settlement["transfers"]
    sections = {
        "Tong_quan": [("Kỳ báo cáo", "Tổng chi (đ)", "Số dư quỹ cuối tháng (đ)", "Số lượt chuyển khoản"),
                      (period, sum(expense.amount for expense, _, _ in rows), balance, len(transfers))],
        "Chi_tiet_chi_tieu": [("Ngày", "Nội dung", "Danh mục", "Số tiền (đ)", "Người ghi", "Nguồn chi"),
            *((expense.created_at.replace(tzinfo=timezone.utc).astimezone(LOCAL).date().isoformat(),
               expense.title, expense.category, expense.amount, name, "Quỹ chung" if fund_id else "Cá nhân")
              for expense, name, fund_id in rows)],
        "Quyet_toan": [("Người trả", "Người nhận", "Số tiền (đ)"),
            *((names[edge["debtor_id"]], names[edge["creditor_id"]], edge["amount"]) for edge in transfers)],
    }
    if format == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output)
        for name, rows in sections.items():
            writer.writerow([name])
            writer.writerows([("'" + cell if isinstance(cell, str) and
                (cell.startswith(("\t", "\r", "\n")) or cell.lstrip().startswith(("=", "+", "-", "@")))
                else cell) for cell in row] for row in rows)
            writer.writerow([])
        content = output.getvalue().encode("utf-8-sig")
        media_type = "text/csv"
    else:
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        from openpyxl.cell import WriteOnlyCell
        for name, rows in sections.items():
            sheet = workbook.create_sheet(name)
            for row in rows:
                cells = [WriteOnlyCell(sheet, value=value) for value in row]
                for cell in cells:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                sheet.append(cells)
        output = BytesIO()
        workbook.save(output)
        content = output.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="quyet-toan-{group_id}-{period}.{format}"',
        "Cache-Control": "no-store",
    })
