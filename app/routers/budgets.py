"""Budgets per category, over a period the user picks.

«بدي أصرف على الأكل ٨٠٠ شيكل كل أسبوعين»: one budget per category and scope
(all, the household, or the user's own), repeating from the day they choose.
Reads return where each one stands today; the numbers come from
`app.services.budgets`, never from the client or a model.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from app.db import get_session
from app.idempotency import remember, replay
from app.models import Budget, BudgetPeriod, Currency, Scope
from app.services import budgets as budget_service
from app.taxonomy import is_valid_category

router = APIRouter()

# Money that is not spending cannot be budgeted: it would be counted twice, or
# not at all.
NOT_BUDGETABLE = {"transfer_in", "transfer_out", "cash_withdrawal", "debt_repayment"}


class BudgetIn(BaseModel):
    category: str
    amount: float = Field(gt=0)
    scope: Scope = Field(default=Scope.ALL, description="all, household («البيت») or personal («إلي»).")
    period: BudgetPeriod = BudgetPeriod.MONTH
    days: int | None = Field(default=None, ge=1, le=366, description="Required for period `days`.")
    starts_on: date | None = Field(default=None, description="Defaults to today.")
    currency: Currency = Currency.ILS
    key: str | None = Field(
        default=None, max_length=128, description="Made at tap, so a replayed queue sets it once."
    )

    @model_validator(mode="after")
    def _days_go_with_days(self):
        if self.period is BudgetPeriod.DAYS and self.days is None:
            raise ValueError("A period of `days` needs `days`.")
        if self.period is not BudgetPeriod.DAYS:
            self.days = None
        return self


class BudgetUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    period: BudgetPeriod | None = None
    days: int | None = Field(default=None, ge=1, le=366)
    starts_on: date | None = None


class BudgetOut(BaseModel):
    id: int
    category: str
    scope: Scope
    amount: float
    currency: Currency
    period: BudgetPeriod
    days: int | None
    starts_on: date
    window_from: date
    window_to: date
    days_total: int
    days_gone: int
    days_left: int = Field(description="Today included.")
    spent: float
    left: float = Field(description="Negative once over.")
    expected_by_now: float = Field(description="Where a straight line through the period would be today.")
    per_day_left: float | None = Field(description="What is left, spread over the days left. None once used up.")
    status: str = Field(description="fine | fast | over. `fast` means clearly ahead of the line.")


def out(session: Session, budget: Budget, on: date) -> BudgetOut:
    s = budget_service.standing(session, budget, on)
    return BudgetOut(
        id=budget.id,
        category=budget.category,
        scope=budget.scope,
        amount=budget.amount,
        currency=budget.currency,
        period=budget.period,
        days=budget.days,
        starts_on=budget.starts_on,
        **s.__dict__,
    )


@router.get("", response_model=list[BudgetOut])
def list_budgets(
    on: date | None = Query(None, description="Defaults to today."),
    session: Session = Depends(get_session),
) -> list[BudgetOut]:
    day = on or date.today()
    return [out(session, b, day) for b in session.exec(select(Budget).order_by(Budget.id))]


@router.post("", response_model=BudgetOut)
def set_budget(budget: BudgetIn, session: Session = Depends(get_session)) -> BudgetOut:
    """Set a budget for a category and scope. 409 if that pair already has one:
    change it with PATCH instead, so two phones cannot leave two limits on it."""
    seen = replay(session, budget.key, "budget")
    if seen is not None:
        return BudgetOut(**seen)
    if not is_valid_category(budget.category):
        raise HTTPException(422, f"Unknown category: {budget.category!r}")
    if budget.category in NOT_BUDGETABLE:
        raise HTTPException(422, f"{budget.category!r} moves money rather than spending it.")
    taken = select(Budget).where(Budget.category == budget.category, Budget.scope == budget.scope)
    if session.exec(taken).first() is not None:
        raise HTTPException(409, "That category already has a budget for this scope. Change it instead.")

    row = Budget(
        category=budget.category,
        scope=budget.scope,
        amount=budget.amount,
        currency=budget.currency,
        period=budget.period,
        days=budget.days,
        starts_on=budget.starts_on or date.today(),
    )
    session.add(row)
    session.flush()
    result = out(session, row, date.today())
    remember(session, budget.key, "budget", result)
    session.commit()
    return result


@router.patch("/{budget_id}", response_model=BudgetOut)
def change_budget(budget_id: int, update: BudgetUpdate, session: Session = Depends(get_session)) -> BudgetOut:
    row = session.get(Budget, budget_id)
    if row is None:
        raise HTTPException(404, "No such budget.")
    if update.amount is not None:
        row.amount = update.amount
    if update.period is not None:
        row.period = update.period
    if update.days is not None:
        row.days = update.days
    if update.starts_on is not None:
        row.starts_on = update.starts_on
    if row.period is BudgetPeriod.DAYS and row.days is None:
        raise HTTPException(422, "A period of `days` needs `days`.")
    if row.period is not BudgetPeriod.DAYS:
        row.days = None
    session.add(row)
    session.commit()
    session.refresh(row)
    return out(session, row, date.today())


@router.delete("/{budget_id}", status_code=204)
def remove_budget(budget_id: int, session: Session = Depends(get_session)) -> Response:
    """404 when already gone, which a replayed queue treats as done."""
    row = session.get(Budget, budget_id)
    if row is None:
        raise HTTPException(404, "No such budget.")
    session.delete(row)
    session.commit()
    return Response(status_code=204)
