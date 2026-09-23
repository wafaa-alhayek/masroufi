"""The three numbers on the home screen.

Chosen because a household can act on each one, which is the test the designer set:
what you have spent against what was expected, how long the cupboard lasts, and what
is about to go off.

Every one of them can be unknown, and each says so rather than showing a zero. A
zero that means "no data" is the most misleading thing a summary screen can do.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.deps import get_temperature_source
from app.models import (
    Currency,
    Item,
    LineState,
    ShoppingLine,
    StockCondition,
    Transaction,
)
from app.services import pantry as pantry_service
from app.services import prices as price_service
from app.services import shopping
from app.weather import TemperatureSource

router = APIRouter()

GROCERY_CATEGORY = "groceries"


class SpendOut(BaseModel):
    currency: Currency
    spent: float = Field(description="Grocery spending recorded for this week.")
    estimate: float | None = Field(
        default=None, description="What the week's remaining list is expected to cost."
    )
    estimate_covers: int = Field(
        default=0, description="How many list lines the estimate could price."
    )
    estimate_missing: int = Field(
        default=0, description="How many it could not, so the estimate understates."
    )
    caveat: str | None = None


class RunwayOut(BaseModel):
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    per_week: float = Field(description="What the current plan uses in a week.")
    weeks_left: float


class AtRiskOut(BaseModel):
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    days_held: int
    keeps_days: float
    note: str


class HomeOut(BaseModel):
    week_start: date
    spend: SpendOut
    runway: list[RunwayOut] = Field(
        description="Longest-lasting last, so the shortest is at the top. Empty when "
        "there is no plan to derive a rate from."
    )
    runway_note: str | None = None
    at_risk: list[AtRiskOut]


@router.get("", response_model=HomeOut)
async def home(
    week_start: date | None = Query(None, description="Defaults to today."),
    currency: Currency = Currency.ILS,
    session: Session = Depends(get_session),
    temperatures: TemperatureSource = Depends(get_temperature_source),
) -> HomeOut:
    start = week_start or date.today()
    end = start + timedelta(days=6)

    items = {item.id: item for item in session.exec(select(Item))}

    return HomeOut(
        week_start=start,
        spend=_spend(session, items, start, end, currency),
        runway=_runway(session, items, start),
        runway_note=None
        if _has_plan(session, start, end)
        else "No meals planned this week, so there is no rate to measure the cupboard against.",
        at_risk=await _at_risk(session, start, temperatures),
    )


def _has_plan(session: Session, start: date, end: date) -> bool:
    from app.models import PlannedMeal

    return bool(
        session.exec(
            select(PlannedMeal).where(
                PlannedMeal.plan_date >= start, PlannedMeal.plan_date <= end
            )
        ).first()
    )


def _spend(
    session: Session,
    items: dict[int, Item],
    start: date,
    end: date,
    currency: Currency,
) -> SpendOut:
    spent = sum(
        abs(t.amount)
        for t in session.exec(
            select(Transaction).where(
                Transaction.booked_on >= start,
                Transaction.booked_on <= end,
                Transaction.category == GROCERY_CATEGORY,
                Transaction.currency == currency,
                Transaction.is_internal_transfer == False,  # noqa: E712
            )
        )
        if t.amount < 0
    )

    pending = list(
        session.exec(
            select(ShoppingLine).where(
                ShoppingLine.week_start == start,
                ShoppingLine.state == LineState.PENDING,
            )
        )
    )
    estimates = price_service.estimates(session, items, currency=currency)

    total = 0.0
    priced = missing = 0
    for line in pending:
        est = estimates.get(line.item_id)
        if est is None:
            missing += 1
            continue
        total += est.per_unit * line.quantity
        priced += 1

    caveat = None
    if pending and missing:
        caveat = (
            f"The estimate covers {priced} of {priced + missing} things still to buy; "
            f"{missing} have no recorded price, so the real total will be higher."
        )
    elif not pending:
        caveat = "Nothing left on this week's list."

    return SpendOut(
        currency=currency,
        spent=round(spent, 2),
        estimate=round(total, 2) if priced else None,
        estimate_covers=priced,
        estimate_missing=missing,
        caveat=caveat,
    )


def _runway(session: Session, items: dict[int, Item], start: date) -> list[RunwayOut]:
    """How long each stocked item lasts at the rate the current plan uses it.

    The plan is the only honest rate available: a household's past consumption is
    not recorded, and a guess would be worse than saying nothing.
    """
    needs = shopping.compute_needs(session, start, use_stock=False)
    if not needs:
        return []

    per_week: dict[int, float] = {}
    for need in needs:
        per_week[need.item.id] = per_week.get(need.item.id, 0.0) + need.required

    on_hand = pantry_service.available(session, set(items))

    out: list[RunwayOut] = []
    for item_id, held in on_hand.items():
        rate = per_week.get(item_id, 0.0)
        item = items.get(item_id)
        if item is None or rate <= 0 or held <= 0:
            # Not used by this plan, so there is no rate and no honest answer.
            continue
        out.append(
            RunwayOut(
                item_id=item_id,
                name_en=item.name_en,
                name_ar=item.name_ar,
                quantity=round(held, 2),
                unit=item.unit.value,
                per_week=round(rate, 2),
                weeks_left=round(held / rate, 1),
            )
        )

    return sorted(out, key=lambda r: r.weeks_left)


async def _at_risk(
    session: Session, on: date, temperatures: TemperatureSource
) -> list[AtRiskOut]:
    reading = (await temperatures.daily_max(on, on)).get(on)
    risks = pantry_service.assess(
        session, on=on, celsius=reading.max_c if reading else None
    )
    return [
        AtRiskOut(
            item_id=r.item_id,
            name_en=r.name_en,
            name_ar=r.name_ar,
            quantity=r.quantity,
            unit=r.unit,
            days_held=r.days_held,
            keeps_days=r.keeps_days,
            note=r.note,
        )
        for r in risks
        if r.note and r.condition is not StockCondition.SPOILED
    ]
