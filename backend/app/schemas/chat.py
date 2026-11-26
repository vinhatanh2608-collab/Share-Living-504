from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PaymentCard(BaseModel):
    proof_id: int
    debt_id: int
    status: str
