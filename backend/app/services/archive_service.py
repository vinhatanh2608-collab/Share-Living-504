"""Move eligible history in bounded, atomic batches."""
from datetime import timedelta

from bson import ObjectId
from sqlalchemy import delete, exists, select

from ..models.archive import debt_archive, group_archive, participant_archive, personal_archive
from ..models.living import (DebtSplit, ExpenseParticipant, GroupExpense, GroupExpenseDraft,
                             GroupFundTransaction, LivingGroup, PersonalTransaction)
from ..models.mysql_models import now


async def move_rows(sql, model, archive, condition):
    source = model.__table__
    await sql.execute(archive.insert().from_select(list(source.c.keys()), select(source).where(condition)))
    await sql.execute(delete(source).where(condition))


def settled_expenses(cutoff):
    expense = GroupExpense.id
    return select(GroupExpense.id, GroupExpense.group_id).where(
        GroupExpense.created_at < cutoff,
        exists().where(DebtSplit.expense_id == expense),
        ~exists().where(DebtSplit.expense_id == expense, DebtSplit.status != "PAID"),
        ~exists().where(GroupExpenseDraft.expense_id == expense),
        ~exists().where(GroupFundTransaction.expense_id == expense),
    ).order_by(GroupExpense.created_at, expense)


async def archive_batch(resources, cutoff, batch_size=100):
    async with resources.sql.begin() as sql:
        ids = list(await sql.scalars(select(PersonalTransaction.id).where(PersonalTransaction.spent_at < cutoff.date())
            .order_by(PersonalTransaction.spent_at, PersonalTransaction.id).limit(batch_size).with_for_update(skip_locked=True)))
        if ids:
            await move_rows(sql, PersonalTransaction, personal_archive, PersonalTransaction.id.in_(ids))
    async with resources.sql.begin() as sql:
        candidate_groups = {row.group_id for row in (await sql.execute(settled_expenses(cutoff).limit(batch_size)))}
    moved = 0
    for group_id in sorted(candidate_groups):
        async with resources.sql.begin() as sql:
            # Same lock as expense/receipt mutations; recheck eligibility after acquiring it.
            if not await sql.get(LivingGroup, group_id, with_for_update=True):
                continue
            rows = await sql.execute(settled_expenses(cutoff).where(GroupExpense.group_id == group_id)
                                     .limit(batch_size - moved).with_for_update())
            expense_ids = [row.id for row in rows]
            if expense_ids:
                await move_rows(sql, DebtSplit, debt_archive, DebtSplit.expense_id.in_(expense_ids))
                await move_rows(sql, ExpenseParticipant, participant_archive, ExpenseParticipant.expense_id.in_(expense_ids))
                await move_rows(sql, GroupExpense, group_archive, GroupExpense.id.in_(expense_ids))
                moved += len(expense_ids)
            if moved == batch_size:
                break
    return len(ids), moved


async def prune_messages(mongo, retention_days, batch_size, *, max_batches=10):
    cutoff = now() - timedelta(days=retention_days)
    total = 0
    filters = (
        (mongo.group_messages, {"created_at": {"$lt": cutoff}}, "created_at"),
        (mongo.ai_chat_history, {"created_at": {"$lt": cutoff}}, "created_at"),
        (mongo.ai_chat_history, {"created_at": {"$exists": False},
                                 "_id": {"$lt": ObjectId.from_datetime(cutoff)}}, "_id"),
    )
    for collection, selector, order in filters:
        for _ in range(max_batches):
            docs = await collection.find(selector, {"_id": 1}).sort(
                order, 1).limit(batch_size).to_list(batch_size)
            if not docs:
                break
            result = await collection.delete_many({"_id": {"$in": [doc["_id"] for doc in docs]}})
            total += result.deleted_count
    return total
