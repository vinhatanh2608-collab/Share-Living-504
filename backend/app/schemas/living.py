from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, StringConstraints, field_validator, model_validator

from ..core.request_security import RECEIPT_LIMIT
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


WholeDong = Annotated[int, Field(ge=0, le=10**12)]


class ParticipantIn(BaseModel):
    user_id: int = Field(gt=0)
    status: Literal["paid", "unpaid", "excluded"]
    paid_amount: WholeDong
    share_amount: WholeDong | None = None

    @field_validator("user_id", "paid_amount", "share_amount", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Expected a whole number")
        return value

    @model_validator(mode="after")
    def validate_amounts(self):
        if (self.status == "paid") != (self.paid_amount > 0):
            raise ValueError("Only contributing members have a positive paid amount")
        if self.status == "excluded" and self.share_amount not in (None, 0):
            raise ValueError("Excluded members must have a zero share")
        return self


class GroupExpenseUpdate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    category: Category
    amount: Money = Field(validation_alias=AliasChoices("amount", "total_amount"))
    participants: Ids | Annotated[list[ParticipantIn], Field(min_length=1)] | None = None
    custom_shares: dict[int, Money] | None = Field(default=None, max_length=25)
    payer_id: int | None = Field(default=None, gt=0)
    split_user_ids: list[Annotated[int, Field(gt=0)]] | None = Field(default=None, max_length=25)


class GroupExpenseIn(GroupExpenseUpdate):
    request_id: UUID


class DebtSelection(BaseModel):
    debt_ids: Ids


class ReminderTransfer(BaseModel):
    debtor_id: int = Field(strict=True, gt=0)
    amount: int = Field(strict=True, gt=0, le=2**63 - 1)


class TransferIn(BaseModel):
    creditor_id: int = Field(strict=True, gt=0)
    amount: int = Field(strict=True, gt=0, le=2**63 - 1)


class ReceiptIn(BaseModel):
    debt_ids: Ids | None = None
    transfer: TransferIn | None = None

    @model_validator(mode="after")
    def check_selection(self):
        if (self.debt_ids is None) == (self.transfer is None):
            raise ValueError("Choose debts or a simplified transfer")
        return self


GROUP_PROOF_MAX_BYTES = RECEIPT_LIMIT


class ReceiptUpload(ReceiptIn):
    filename: str | None = None
    size: int = Field(strict=True, gt=0, le=GROUP_PROOF_MAX_BYTES)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]


class ReceiptSubmit(ReceiptIn):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    upload_id: UUID


class VerifyIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reject_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None


class ChatIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
