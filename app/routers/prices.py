"""Prices: observations in, current estimates out."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.models import Currency, Item, PriceObservation, PriceSource
from app.services import prices as price_service
from app.services import shopping

router = APIRouter()


class ObservationIn(BaseModel):
    item_id: int
    paid: float = Field(gt=0)
    quantity: float = Field(gt=0, description="In the item's own unit.")
    currency: Currency = Currency.ILS
    observed_on: date | None = None
    source: PriceSource = PriceSource.MANUAL
    vendor_id: int | None = None
    note: str = ""


class ObservationOut(BaseModel):
    id: int
    item_id: int
    per_unit: float
    paid: float
    quantity: float
    currency: Currency
    source: PriceSource
    observed_on: date
    note: str


class EstimateOut(BaseModel):
    item_id: int
    name_en: str
    name_ar: str
    currency: Currency
    price: float = Field(description="Per kilo or litre for bulk goods, per item otherwise.")
    quoted_per: float
    unit: str
    low: float
    high: float
    spread_pct: float
    observations: int
    newest_days_old: int
    stale: bool = Field(
        description="Nothing recent enough to rely on. Ask the household rather than "
        "planning around it."
    )
    certainty: str = Field(description="good, fair or poor — by documented rules.")
    sources: list[str]


class LineCost(BaseModel):
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    buy_on: date | None
    estimate: EstimateOut | None = Field(
        default=None, description="Null when nothing is known about this price yet."
    )
    cost: float | None = None


class ListCost(BaseModel):
    week_start: date
    currency: Currency
    known_cost: float = Field(description="Total for the lines a price is known for.")
    lines_priced: int
    lines_unpriced: int
    stale_lines: int
    lines: list[LineCost]
    caveat: str


@router.post("/observations", response_model=ObservationOut)
def add_observation(
    body: ObservationIn, session: Session = Depends(get_session)
) -> PriceObservation:
    """Record a price — typed in by the household, or read off a shelf.

    This is also how a correction works. It is not an override that sticks: it is a
    fresh, heavily weighted observation, which is what keeps the estimate current
    instead of accumulating stale fixes.
    """
    item = session.get(Item, body.item_id)
    if item is None:
        raise HTTPException(404, "No such item.")

    row = price_service.record(
        session,
        item,
        paid=body.paid,
        quantity=body.quantity,
        source=body.source,
        currency=body.currency,
        observed_on=body.observed_on,
        vendor_id=body.vendor_id,
        note=body.note,
    )
    if row is None:
        raise HTTPException(422, "A price needs a positive amount and quantity.")
    session.commit()
    session.refresh(row)
    return row


@router.get("/observations/{item_id}", response_model=list[ObservationOut])
def list_observations(
    item_id: int,
    limit: int = Query(50, le=500),
    session: Session = Depends(get_session),
) -> list[PriceObservation]:
    """The history behind an estimate, newest first, so a figure can be audited."""
    if session.get(Item, item_id) is None:
        raise HTTPException(404, "No such item.")
    return list(
        session.exec(
            select(PriceObservation)
            .where(PriceObservation.item_id == item_id)
            .order_by(PriceObservation.observed_on.desc())
            .limit(limit)
        )
    )


@router.get("/estimate/{item_id}", response_model=EstimateOut)
def read_estimate(
    item_id: int,
    on: date | None = None,
    currency: Currency = Currency.ILS,
    session: Session = Depends(get_session),
) -> EstimateOut:
    """The current best guess for one item.

    404 when nothing is known. That is deliberate: inventing a figure here would
    cost somebody money.
    """
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "No such item.")

    got = price_service.estimate(session, item, on=on, currency=currency)
    if got is None:
        raise HTTPException(
            404,
            f"No price has been recorded for {item.name_en}. Add an observation "
            "rather than relying on a guess.",
        )
    return _estimate_out(item, got)


@router.get("/estimates", response_model=list[EstimateOut])
def read_estimates(
    on: date | None = None,
    currency: Currency = Currency.ILS,
    stale_only: bool = False,
    session: Session = Depends(get_session),
) -> list[EstimateOut]:
    """Everything a price is known for. `stale_only` finds what needs re-checking."""
    items = {i.id: i for i in session.exec(select(Item))}
    found = price_service.estimates(session, items, on=on, currency=currency)
    out = [_estimate_out(items[item_id], est) for item_id, est in found.items()]
    if stale_only:
        out = [e for e in out if e.stale]
    return sorted(out, key=lambda e: (not e.stale, e.name_en))


@router.get("/shopping-list/cost", response_model=ListCost)
def cost_list(
    week_start: date = Query(...),
    currency: Currency = Currency.ILS,
    session: Session = Depends(get_session),
) -> ListCost:
    """What a week's shopping is likely to cost, and how much of that is guesswork.

    The unpriced and stale counts are reported alongside the total on purpose. A
    number that silently covers half the list is worse than a number with its gaps
    named.
    """
    lines = list(
        session.exec(
            select(shopping.ShoppingLine).where(
                shopping.ShoppingLine.week_start == week_start,
                shopping.ShoppingLine.state == shopping.LineState.PENDING,
            )
        )
    )
    items = {i.id: i for i in session.exec(select(Item))}
    found = price_service.estimates(session, items, currency=currency)

    out: list[LineCost] = []
    total = 0.0
    priced = unpriced = stale = 0

    for line in lines:
        item = items.get(line.item_id)
        if item is None:
            continue
        est = found.get(item.id)

        cost = None
        if est is not None:
            cost = round(est.per_unit * line.quantity, 2)
            total += cost
            priced += 1
            if est.stale:
                stale += 1
        else:
            unpriced += 1

        out.append(
            LineCost(
                item_id=item.id,
                name_en=item.name_en,
                name_ar=item.name_ar,
                quantity=line.quantity,
                unit=line.unit.value,
                buy_on=line.buy_on,
                estimate=_estimate_out(item, est) if est else None,
                cost=cost,
            )
        )

    caveat = _caveat(priced, unpriced, stale)
    return ListCost(
        week_start=week_start,
        currency=currency,
        known_cost=round(total, 2),
        lines_priced=priced,
        lines_unpriced=unpriced,
        stale_lines=stale,
        lines=sorted(out, key=lambda line: (line.buy_on is None, line.buy_on or date.min)),
        caveat=caveat,
    )


def _caveat(priced: int, unpriced: int, stale: int) -> str:
    if priced == 0:
        return "No prices are known yet, so this list cannot be costed at all."
    parts = [f"Covers {priced} of {priced + unpriced} lines."]
    if unpriced:
        parts.append(f"{unpriced} have no recorded price.")
    if stale:
        parts.append(f"{stale} rely on a price older than a month.")
    return " ".join(parts)


def _estimate_out(item: Item, est) -> EstimateOut:
    return EstimateOut(
        item_id=item.id,
        name_en=item.name_en,
        name_ar=item.name_ar,
        currency=est.currency,
        price=est.price,
        quoted_per=est.quoted_per,
        unit=item.unit.value,
        low=est.low,
        high=est.high,
        spread_pct=est.spread_pct,
        observations=est.observations,
        newest_days_old=est.newest_days_old,
        stale=est.stale,
        certainty=est.certainty,
        sources=est.sources,
    )
