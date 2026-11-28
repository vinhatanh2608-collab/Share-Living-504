from datetime import date, datetime

from sqlalchemy import BigInteger, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .mysql_models import Base, now


class LivingGroup(Base):
    __tablename__ = "living_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    auto_monthly_reminder: Mapped[bool] = mapped_column(default=True, server_default="1")
    reminder_hour: Mapped[int] = mapped_column(default=20, server_default="20")
    last_reminded_period: Mapped[str | None] = mapped_column(String(7))


class MonthlyReminder(Base):
    __tablename__ = "monthly_group_reminders"
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    transfers: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)
    delivered: Mapped[bool] = mapped_column(default=False)
    next_attempt: Mapped[datetime] = mapped_column(default=now, index=True)


class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(12))
    bank_bin: Mapped[str | None] = mapped_column(String(6))
    bank_account_no: Mapped[str | None] = mapped_column(String(32))
    bank_account_name: Mapped[str | None] = mapped_column(String(100))


class PersonalTransaction(Base):
    __tablename__ = "personal_expenses"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(7))
    category: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(String(250))
    spent_at: Mapped[date]


class Budget(Base):
    __tablename__ = "monthly_budgets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    month: Mapped[date] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(16), primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(BigInteger)


class GroupExpense(Base):
    __tablename__ = "group_expenses"
    __table_args__ = (UniqueConstraint("group_id", "creditor_id", "request_id"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), index=True)
    creditor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)
    payer_share: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(default=now)


class ExpenseParticipant(Base):
    __tablename__ = "group_expense_participants"
    expense_id: Mapped[int] = mapped_column(ForeignKey("group_expenses.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(8))
    paid_amount: Mapped[int] = mapped_column(BigInteger)
    share_amount: Mapped[int] = mapped_column(BigInteger)


class GroupProof(Base):
    __tablename__ = "group_payment_proofs"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), index=True)
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creditor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    upload_id: Mapped[str] = mapped_column(String(36), unique=True)
    object_key: Mapped[str] = mapped_column(String(255))
    debt_ids: Mapped[list[int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")
    transfer_amount: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))
    reject_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=now)


class DebtSplit(Base):
    __tablename__ = "debt_splits"
    __table_args__ = (UniqueConstraint("expense_id", "debtor_id"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("group_expenses.id"))
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="UNPAID")
    proof_id: Mapped[int | None] = mapped_column(ForeignKey("group_payment_proofs.id"))
    reminded_at: Mapped[datetime | None]


class ReminderCooldown(Base):
    __tablename__ = "group_reminder_cooldowns"
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), primary_key=True)
    creditor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    reminded_at: Mapped[datetime]


class GroupReminder(Base):
    __tablename__ = "group_reminders"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sender_name: Mapped[str] = mapped_column(String(200))
    debtor_ids: Mapped[list[int]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)
    next_attempt: Mapped[datetime] = mapped_column(default=now, index=True)


class GroupEvent(Base):
    __tablename__ = "group_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    proof_id: Mapped[int] = mapped_column(ForeignKey("group_payment_proofs.id"))
    delivered: Mapped[bool] = mapped_column(default=False, index=True)
    next_attempt: Mapped[datetime] = mapped_column(default=now)



PERFORMANCE_INDEXES = (
    Index("ix_personal_expenses_user_spent", PersonalTransaction.user_id, PersonalTransaction.spent_at),
    Index("ix_group_members_user_status_group", GroupMember.user_id, GroupMember.status, GroupMember.group_id),
    Index("ix_group_expenses_creditor_group_created", GroupExpense.creditor_id,
          GroupExpense.group_id, GroupExpense.created_at),
    Index("ix_group_events_delivery_schedule", GroupEvent.delivered, GroupEvent.next_attempt, GroupEvent.id),
)
class GroupFundTransaction(Base):
    __tablename__ = "group_fund_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tx_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(250))
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("group_expenses.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class GroupExpenseTemplate(Base):
    __tablename__ = "group_expense_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(16))
    default_amount: Mapped[int] = mapped_column(BigInteger)
    meter_unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    last_meter_reading: Mapped[int | None] = mapped_column(BigInteger)
    participants_preset_json: Mapped[list] = mapped_column(JSON)


class GroupExpenseDraft(Base):
    __tablename__ = "group_expense_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("group_expenses.id"))


class GroupBudgetLimit(Base):
    __tablename__ = "group_budget_limits"
    group_id: Mapped[int] = mapped_column(ForeignKey("living_groups.id"), primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(BigInteger)


PERFORMANCE_INDEXES += (
    Index("ix_personal_user_category_spent", PersonalTransaction.user_id, PersonalTransaction.category,
          PersonalTransaction.spent_at),
    Index("ix_personal_archive_cutoff", PersonalTransaction.spent_at, PersonalTransaction.id),
    Index("ix_group_expense_group_created", GroupExpense.group_id, GroupExpense.created_at, GroupExpense.id),
    Index("ix_group_expense_archive_cutoff", GroupExpense.created_at, GroupExpense.id),
    Index("ix_group_proof_group_status_created", GroupProof.group_id, GroupProof.status, GroupProof.created_at),
    Index("ix_group_proof_debtor_status", GroupProof.debtor_id, GroupProof.status),
    Index("ix_group_proof_creditor_status", GroupProof.creditor_id, GroupProof.status),
    Index("ix_group_debt_debtor_status", DebtSplit.debtor_id, DebtSplit.status),
)


class AdvisorAction(Base):
    __tablename__ = "advisor_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tool: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    changes: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(default=now)
class AdvisorOverallBudget(Base):
    __tablename__ = "advisor_overall_budgets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    month: Mapped[date] = mapped_column(primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(BigInteger)
