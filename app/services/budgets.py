"""Where a budget stands today.

A budget is a category, an amount and a repeating period. Three questions are
answered here, all from code and none from a model:

  * which window today falls in (`window`);
  * how much of it is spent, by the same rules as `/api/summary` — money out in
    that category, with internal transfers and excluded rows left out (`spent`);
  * whether it is going faster than the days are (`status`).

The pace is a straight line: by the middle of a fortnight, half the money. That
is crude for a category bought in lumps (one gas cylinder a month), so «fast» is
only reported once the spending is clearly ahead of the line, and never on the
first day.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import Budget, BudgetPeriod, Scope, Transaction

FIXED_DAYS = {BudgetPeriod.WEEK: 7, BudgetPeriod.FORTNIGHT: 14}

# How far ahead of the straight line spending must be before it is called fast.
FAST_MARGIN = 1.15


def _month_anchor(year: int, month: int, day: int) -> date:
    """The anchor day in a month, clamped: a budget from the 31st starts on the
    30th in a 30-day month."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, by: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + by
    return index // 12, index % 12 + 1


def window(budget: Budget, on: date) -> tuple[date, date]:
    """First and last day of the period that `on` falls in.

    Before `starts_on` there is no earlier window; the first one is returned.
    """
    start = budget.starts_on
    if budget.period is BudgetPeriod.MONTH:
        day = start.day
        if on < start:
            year, month = start.year, start.month
        else:
            year, month = on.year, on.month
            if on < _month_anchor(year, month, day):
                year, month = _shift_month(year, month, -1)
        begin = _month_anchor(year, month, day)
        ny, nm = _shift_month(year, month, 1)
        return begin, _month_anchor(ny, nm, day) - timedelta(days=1)

    length = FIXED_DAYS.get(budget.period) or budget.days or 30
    index = max(0, (on - start).days // length)
    begin = start + timedelta(days=index * length)
    return begin, begin + timedelta(days=length - 1)


def spent(session: Session, category: str, begin: date, end: date, scope: Scope = Scope.ALL) -> float:
    statement = select(Transaction).where(
        Transaction.category == category,
        Transaction.booked_on >= begin,
        Transaction.booked_on <= end,
        Transaction.amount < 0,
        Transaction.is_internal_transfer == False,  # noqa: E712
        Transaction.excluded == False,  # noqa: E712
    )
    if scope is Scope.HOUSEHOLD:
        statement = statement.where(Transaction.personal == False)  # noqa: E712
    elif scope is Scope.PERSONAL:
        statement = statement.where(Transaction.personal == True)  # noqa: E712
    return round(sum(-t.amount for t in session.exec(statement)), 2)


@dataclass
class Standing:
    window_from: date
    window_to: date
    days_total: int
    days_gone: int
    days_left: int
    spent: float
    left: float
    expected_by_now: float
    per_day_left: float | None
    status: str  # fine | fast | over


def standing(session: Session, budget: Budget, on: date) -> Standing:
    begin, end = window(budget, on)
    total = (end - begin).days + 1
    gone = min(total, max(0, (on - begin).days + 1))
    days_left = total - gone + (1 if gone else 0)  # today still counts as left
    used = spent(session, budget.category, begin, end, budget.scope)
    expected = budget.amount * gone / total
    left = round(budget.amount - used, 2)

    if used > budget.amount:
        status = "over"
    elif gone > 1 and used > expected * FAST_MARGIN:
        status = "fast"
    else:
        status = "fine"

    return Standing(
        window_from=begin,
        window_to=end,
        days_total=total,
        days_gone=gone,
        days_left=days_left,
        spent=used,
        left=left,
        expected_by_now=round(expected, 2),
        per_day_left=round(left / days_left, 2) if left > 0 and days_left else None,
        status=status,
    )
