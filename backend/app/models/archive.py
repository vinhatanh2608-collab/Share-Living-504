"""Archived rows mirror their live schema. Historical queries use an explicit union."""
from sqlalchemy import Column, Index, Table, select, union_all
from sqlalchemy.orm import aliased

from .living import DebtSplit, ExpenseParticipant, GroupExpense, PersonalTransaction
from .mysql_models import Base


def archive_table(source, name, keys):
    table = Table(name, Base.metadata, *(Column(column.name, column.type,
        primary_key=column.primary_key, nullable=column.nullable, autoincrement=False)
        for column in source.__table__.columns))
    Index(f"ix_{name}_lookup", *(table.c[key] for key in keys))
    return table


personal_archive = archive_table(PersonalTransaction, "expenses_archive", ("user_id", "spent_at"))
group_archive = archive_table(GroupExpense, "group_expenses_archive", ("group_id", "created_at"))
debt_archive = archive_table(DebtSplit, "debts_archive", ("expense_id", "debtor_id"))
participant_archive = archive_table(ExpenseParticipant, "expense_participants_archive", ("expense_id", "user_id"))


def history(model, archive):
    return aliased(model, union_all(select(model.__table__), select(archive)).subquery(), adapt_on_names=True)


PersonalHistory = history(PersonalTransaction, personal_archive)
GroupHistory = history(GroupExpense, group_archive)
DebtHistory = history(DebtSplit, debt_archive)
ParticipantHistory = history(ExpenseParticipant, participant_archive)


Index("ix_group_expense_archive_request", group_archive.c.group_id, group_archive.c.creditor_id,
      group_archive.c.request_id, unique=True)
