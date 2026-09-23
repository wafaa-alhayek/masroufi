"""What something costs, which is never a fact — only a dated observation.

In a market where WFP has recorded food prices moving by tens of percent inside a
month, storing "rice costs 5.60" is worse than storing nothing: it is wrong
silently, and someone plans around it.

So there is no price column anywhere. There is a log of observations, each with a
date, a source and a shop, and a current estimate computed on demand from them:

  * Recent observations count for more. Weight halves every `PRICE_HALF_LIFE_DAYS`,
    so a price from last week dominates one from three months ago rather than
    being averaged flat into it.
  * First-hand beats reference. What the household actually paid, or read off a
    shelf, outweighs a regional market average.
  * The spread is reported, not hidden. When recent observations disagree, a
    range is the honest answer and a single number is not.
  * No observations means no estimate. The app returns nothing rather than
    inventing a figure, because a made-up price here loses someone money.

A manual edit is not an override that sticks. It is a fresh, heavily weighted
observation, which is what makes the whole thing stay current instead of
accumulating stale "corrections".
"""

import math
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

from app.config import settings
from app.models import Currency, Item, PriceObservation, PriceSource, Unit

# How much a source is trusted before age is considered. First-hand prices from
# this household's own shops beat a regional average for a different market.
SOURCE_WEIGHT: dict[PriceSource, float] = {
    PriceSource.PURCHASE: 1.0,
    PriceSource.MANUAL: 1.0,
    PriceSource.RECEIPT: 1.0,
    PriceSource.CROWD: 0.7,
    PriceSource.REFERENCE: 0.6,
}

# Units sold by weight or volume are quoted per kilo or litre, which is also how
# the WFP and PCBS datasets publish. Everything else is per item.
BULK_UNITS = frozenset({Unit.GRAM, Unit.ML})


@dataclass
class Estimate:
    item_id: int
    currency: Currency
    per_unit: float
    quoted_per: float = 1000.0
    price: float = 0.0
    observations: int = 0
    newest_days_old: int = 0
    low: float = 0.0
    high: float = 0.0
    stale: bool = False
    certainty: str = "poor"
    sources: list[str] = field(default_factory=list)

    @property
    def spread_pct(self) -> float:
        if self.price <= 0:
            return 0.0
        return round((self.high - self.low) / self.price * 100, 1)


def quoted_per(item: Item) -> float:
    return 1000.0 if item.unit in BULK_UNITS else 1.0


def record(
    session: Session,
    item: Item,
    paid: float,
    quantity: float,
    source: PriceSource,
    currency: Currency = Currency.ILS,
    observed_on: date | None = None,
    vendor_id: int | None = None,
    note: str = "",
) -> PriceObservation | None:
    """Log what something cost. Returns None if the numbers cannot yield a price."""
    if paid <= 0 or quantity <= 0:
        return None

    row = PriceObservation(
        item_id=item.id,
        per_unit=abs(paid) / quantity,
        currency=currency,
        quantity=quantity,
        paid=abs(paid),
        source=source,
        observed_on=observed_on or date.today(),
        vendor_id=vendor_id,
        note=note,
    )
    session.add(row)
    return row


def estimate(
    session: Session,
    item: Item,
    on: date | None = None,
    currency: Currency = Currency.ILS,
) -> Estimate | None:
    """The current best guess, or None when there is nothing to guess from."""
    rows = [
        r
        for r in session.exec(
            select(PriceObservation).where(
                PriceObservation.item_id == item.id,
                PriceObservation.currency == currency,
            )
        )
    ]
    return _from_rows(item, rows, on or date.today(), currency)


def estimates(
    session: Session,
    items: dict[int, Item],
    on: date | None = None,
    currency: Currency = Currency.ILS,
) -> dict[int, Estimate]:
    """Estimates for many items in one pass, for costing a whole list."""
    if not items:
        return {}

    today = on or date.today()
    by_item: dict[int, list[PriceObservation]] = {}
    for row in session.exec(
        select(PriceObservation).where(
            PriceObservation.item_id.in_(set(items)),
            PriceObservation.currency == currency,
        )
    ):
        by_item.setdefault(row.item_id, []).append(row)

    out: dict[int, Estimate] = {}
    for item_id, rows in by_item.items():
        got = _from_rows(items[item_id], rows, today, currency)
        if got is not None:
            out[item_id] = got
    return out


def _from_rows(
    item: Item, rows: list[PriceObservation], today: date, currency: Currency
) -> Estimate | None:
    if not rows:
        return None

    half_life = max(settings.price_half_life_days, 0.5)
    weighted_sum = weight_total = 0.0
    recent: list[float] = []
    newest_age = 10**6
    sources: set[str] = set()

    for row in rows:
        age = max((today - row.observed_on).days, 0)
        weight = SOURCE_WEIGHT.get(row.source, 0.5) * math.pow(0.5, age / half_life)
        if weight <= 0:
            continue

        weighted_sum += row.per_unit * weight
        weight_total += weight
        newest_age = min(newest_age, age)
        sources.add(row.source.value)
        # The spread is taken over observations still carrying real weight, so a
        # price from last year does not widen today's range.
        if age <= settings.price_spread_window_days:
            recent.append(row.per_unit)

    if weight_total <= 0:
        return None

    per_unit = weighted_sum / weight_total
    span = recent or [per_unit]
    scale = quoted_per(item)

    return Estimate(
        item_id=item.id,
        currency=currency,
        per_unit=round(per_unit, 6),
        quoted_per=scale,
        price=round(per_unit * scale, 2),
        observations=len(rows),
        newest_days_old=newest_age,
        low=round(min(span) * scale, 2),
        high=round(max(span) * scale, 2),
        stale=newest_age > settings.price_stale_after_days,
        certainty=_certainty(len(recent), newest_age, min(span), max(span), per_unit),
        sources=sorted(sources),
    )


def _certainty(
    recent_count: int, newest_age: int, low: float, high: float, per_unit: float
) -> str:
    """A coarse bucket, by documented rules, rather than a fabricated percentage.

    Three words a screen can act on beats a 0.87 nobody can interpret.
    """
    if newest_age > settings.price_stale_after_days:
        return "poor"

    spread = (high - low) / per_unit if per_unit > 0 else 1.0
    if recent_count >= 3 and spread <= 0.25 and newest_age <= 14:
        return "good"
    if recent_count >= 1 and spread <= 0.6:
        return "fair"
    return "poor"
