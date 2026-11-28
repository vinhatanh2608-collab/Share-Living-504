"""Validate advisor tool arguments at the boundary; identity never comes from the model."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .living import Category, Money

Month = Annotated[str, Field(pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")]
GroupId = Annotated[int, Field(strict=True, gt=0, le=2147483647)]


class Tool(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Analytics(Tool):
    month: Month | None = None
    category: Category | None = None


class Search(Tool):
    keyword: str | None = Field(default=None, max_length=100)
    category: Category | None = None
    limit: int = Field(default=10, strict=True, ge=1, le=25)


class Debts(Tool):
    group_id: GroupId | None = None


class ExpenseItem(Tool):
    amount: Money
    category: Category
    note: str = Field(min_length=1, max_length=250)
    spent_at: date


class Expenses(Tool):
    items: list[ExpenseItem] = Field(min_length=1, max_length=10)


class GroupExpenseTool(Tool):
    group_id: GroupId
    amount: Money
    description: str = Field(min_length=1, max_length=200)
    split_type: Literal["equal", "clarify"]


class BudgetTool(Tool):
    category: Category | Literal["OVERALL"]
    limit_amount: Money
    month: Month | None = None


class Undo(Tool):
    action_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


class Correction(Tool):
    action_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    amount: Money


TOOLS = {
    "get_spending_analytics": Analytics,
    "search_transactions": Search,
    "get_group_debts_summary": Debts,
    "record_personal_expenses": Expenses,
    "create_group_expense_draft_or_record": GroupExpenseTool,
    "upsert_monthly_budget": BudgetTool,
    "undo_last_ai_action": Undo,
}

WRITE_TOOLS = {"record_personal_expenses", "create_group_expense_draft_or_record", "upsert_monthly_budget"}
