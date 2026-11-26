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
    __table_args__ = (UniqueConstraint("group_id", "creditor_id", "request_id"),)
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
    reject_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=now)


class DebtSplit(Base):
    __tablename__ = "debt_splits"
    __table_args__ = (UniqueConstraint("expense_id", "debtor_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("group_expenses.id"))
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="UNPAID")
    proof_id: Mapped[int | None] = mapped_column(ForeignKey("group_payment_proofs.id"))
    reminded_at: Mapped[datetime | None]


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
