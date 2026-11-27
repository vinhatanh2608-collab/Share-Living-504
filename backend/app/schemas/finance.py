from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, Field


class ExpenseIn(BaseModel):
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    amount: int = Field(gt=0, le=10**12, strict=True)
    participants: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1, max_length=8)
    custom_shares: dict[int, Annotated[int, Field(gt=0, le=10**12, strict=True)]] | None = Field(default=None, max_length=8)
    due_at: AwareDatetime


class DebtSplit(BaseModel):
    id: int
    expense_id: int
    debtor_id: int
    creditor_id: int
    amount: int
    status: str
    due_at: datetime
    title: str = ""


class PaymentProof(BaseModel):
    upload_id: UUID


class ProofDecision(BaseModel):
    approve: bool = Field(strict=True)


LEGACY_PROOF_MAX_BYTES = 2_000_000


class ProofUpload(BaseModel):
    filename: str | None = None
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size: int = Field(gt=0, le=LEGACY_PROOF_MAX_BYTES, strict=True)


class BatchUpload(ProofUpload):
    debt_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=1, max_length=25)


class BatchSubmit(PaymentProof):
    debt_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=1, max_length=25)
