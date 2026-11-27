import asyncio
import calendar
import logging
from datetime import datetime, timedelta, timezone

from pymongo.errors import PyMongoError
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from ..models.living import GroupMember, LivingGroup, MonthlyReminder
from ..models.mysql_models import now
from .debt_simplifier import simplify_group

# Vietnam observes UTC+07:00 year-round.
LOCAL = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
logger = logging.getLogger(__name__)


async def queue_month(sql, group, at, *, manual=False):
    local = at.astimezone(LOCAL)
    period = local.strftime("%Y-%m")
    if not manual and (not group.auto_monthly_reminder or local.hour < group.reminder_hour
                       or local.day != calendar.monthrange(local.year, local.month)[1]
                       or group.last_reminded_period == period):
        return {"status": "skipped", "period": period}
    balances = await simplify_group(sql, group.id)
    names = {member["user_id"]: member["name"] for member in balances["member_breakdown"]}
    transfers = [{"debtor_id": edge["debtor_id"], "creditor_id": edge["creditor_id"],
                  "debtor_name": names[edge["debtor_id"]], "creditor_name": names[edge["creditor_id"]],
                  "amount": edge["amount"]} for edge in balances["transfers"]]
    if not transfers:
        return {"status": "empty", "period": period}
    record = await sql.get(MonthlyReminder, (group.id, period), with_for_update=True)
    if record is None:
        record = MonthlyReminder(group_id=group.id, period=period, sender_id=group.owner_id)
        sql.add(record)
    record.transfers = transfers
    record.created_at = record.next_attempt = now()
    record.delivered = False
    group.last_reminded_period = period
    return {"status": "sent", "period": period}


async def project_monthly(resources, manager, key=None):
    async with resources.sql.begin() as sql:
        query = select(MonthlyReminder).where(MonthlyReminder.delivered.is_(False),
                                               MonthlyReminder.next_attempt <= now())
        if key:
            query = query.where(MonthlyReminder.group_id == key[0], MonthlyReminder.period == key[1])
        record = await sql.scalar(query.order_by(MonthlyReminder.next_attempt).limit(1).with_for_update())
        if record is None:
            return False
        year, month = record.period.split("-")
        row = {"_id": f"monthly:{record.group_id}:{record.period}", "kind": "MONTHLY_REMINDER",
               "group_id": record.group_id, "sender_id": record.sender_id, "period": record.period,
               "title": f"Tổng kết & Nhắc nợ cuối tháng {month}/{year}", "created_at": record.created_at,
               "transfers": record.transfers, "total_amount": sum(edge["amount"] for edge in record.transfers)}
        try:
            await resources.mongo.group_messages.replace_one({"_id": row["_id"]}, row, upsert=True)
        except PyMongoError:
            logger.warning("Monthly reminder projection delayed", exc_info=True)
            record.next_attempt = now() + timedelta(seconds=10)
            return False
        recipients = set(await sql.scalars(select(GroupMember.user_id).where(
            GroupMember.group_id == record.group_id, GroupMember.status == "APPROVED")))
        record.delivered = True
    row["id"] = row.pop("_id")
    row["created_at"] = row["created_at"].replace(tzinfo=timezone.utc).isoformat()
    manager.publish({"type": "group_chat", "group_id": row["group_id"], "message": row}, recipients)
    return True


async def run_due(resources, manager, at=None):
    local = (at or datetime.now(LOCAL)).astimezone(LOCAL)
    if local.day != calendar.monthrange(local.year, local.month)[1]:
        return 0
    period = local.strftime("%Y-%m")
    async with resources.sql() as sql:
        ids = list(await sql.scalars(select(LivingGroup.id).where(LivingGroup.auto_monthly_reminder.is_(True),
            LivingGroup.reminder_hour <= local.hour, or_(LivingGroup.last_reminded_period.is_(None),
                                                        LivingGroup.last_reminded_period != period))))
    sent = 0
    for group_id in ids:
        async with resources.sql.begin() as sql:
            group = await sql.get(LivingGroup, group_id, with_for_update=True)
            if group is None:
                continue
            result = await queue_month(sql, group, local)
        if result["status"] == "sent":
            await project_monthly(resources, manager, (group_id, period))
            sent += 1
    return sent


async def monthly_forever(resources, manager):
    while True:
        try:
            await run_due(resources, manager)
        except (SQLAlchemyError, PyMongoError):
            logger.warning("Monthly reminder scan delayed", exc_info=True)
        await asyncio.sleep(15 * 60)
