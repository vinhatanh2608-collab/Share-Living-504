from datetime import datetime, timezone
from sqlalchemy import ForeignKey, String, BigInteger, JSON, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)
    bank_bin: Mapped[str | None] = mapped_column(String(6))
    account_number: Mapped[str | None] = mapped_column(String(32))
    account_name: Mapped[str | None] = mapped_column(String(100))


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (UniqueConstraint("creditor_id", "request_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    creditor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[int] = mapped_column(BigInteger)
    due_at: Mapped[datetime]


class Debt(Base):
    __tablename__ = "debts"
    __table_args__ = (UniqueConstraint("expense_id", "debtor_id"),
                      Index("ix_debt_debtor_state", "debtor_id", "status"))
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id"))
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="open")
    reminded_at: Mapped[datetime | None]


class Proof(Base):
    __tablename__ = "proofs"
    id: Mapped[int] = mapped_column(primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id"), index=True)
    upload_id: Mapped[str] = mapped_column(String(36), unique=True)
    object_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    submitted_at: Mapped[datetime] = mapped_column(default=now)
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_delivery", "delivered", "next_attempt"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSON)
    delivered: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt: Mapped[datetime] = mapped_column(default=now)
