from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from .auth import Login

Category = Literal["FOOD", "TRANSPORT", "HOUSING", "STUDY", "ENTERTAINMENT", "OTHER"]
Money = Annotated[int, Field(strict=True, gt=0, le=10**12)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Ids = Annotated[list[Annotated[int, Field(strict=True, gt=0)]], Field(min_length=1, max_length=25)]


class Register(Login):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
    password: str = Field(min_length=12, max_length=72)
    full_name: Name

    @field_validator("password")
    @classmethod
    def password_complexity(cls, value):
        if (not any(char.isalpha() for char in value)
                or not any(char.isdigit() or (not char.isalnum() and not char.isspace()) for char in value)
                or len(set(value.casefold())) < 6):
            raise ValueError("Use letters and at least one number or symbol, with varied characters")
        return value


class GroupIn(BaseModel):
    name: Name


class JoinIn(BaseModel):
    group_code: str = Field(pattern=r"^GRP-[A-Z0-9]{6,12}$")

    @field_validator("group_code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class MemberDecision(BaseModel):
    status: Literal["APPROVED", "REJECTED"]


class BankInfo(BaseModel):
    bank_bin: str = Field(pattern=r"^\d{6}$")
    bank_account_no: str = Field(pattern=r"^\d{4,32}$")
    bank_account_name: Name


class TransactionIn(BaseModel):
    type: Literal["EXPENSE", "INCOME"]
    category: Category
    amount: Money
    note: str = Field(default="", max_length=250)
    spent_at: date


class BudgetIn(BaseModel):
    category: Category
    monthly_limit: Money
    month: date

    @field_validator("month")
    @classmethod
    def month_start(cls, value: date):
        if value.day != 1:
            raise ValueError("Use the first day of the month")
        return value


class GroupExpenseIn(BaseModel):
    request_id: UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    category: Category
    amount: Money
    participants: Ids
    custom_shares: dict[int, Money] | None = Field(default=None, max_length=25)


class ReceiptIn(BaseModel):
    debt_ids: Ids


GROUP_PROOF_MAX_BYTES = 299_999


class ReceiptUpload(ReceiptIn):
    size: int = Field(strict=True, gt=0, le=GROUP_PROOF_MAX_BYTES)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]


class ReceiptSubmit(ReceiptIn):
    upload_id: UUID


class VerifyIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reject_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None


class ChatIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
