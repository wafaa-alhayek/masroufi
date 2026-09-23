"""Turn a week of planned meals into two shopping lists.

The split is by shelf life, not by meal. With refrigeration scarce, anything
perishable is listed on the day it is cooked and bought that day; anything that
keeps is aggregated across the whole week into one shop, which is both fewer trips
and a better unit price.

Aggregation happens **before** rounding to a purchasable amount. Rounding first
would multiply the same rounding waste by every meal in the week: seven days of
80 g of rice rounded up to 250 g each is 1.75 kg instead of 750 g.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import Session, select

from app.kitchen import get_household
from app.kitchen.spoilage import REFERENCE_C, keeps_for
from app.weather import DayTemperature
from app.models import (
    Dish,
    DishItem,
    DishPortion,
    Item,
    ItemRole,
    LineState,
    PlannedMeal,
    ShelfLife,
    ShoppingLine,
)


@dataclass
class Need:
    item: Item
    quantity: float
    buy_on: date | None
    dishes: list[str] = field(default_factory=list)


def week_bounds(week_start: date) -> tuple[date, date]:
    return week_start, week_start + timedelta(days=6)


def compute_needs(
    session: Session,
    week_start: date,
    temperatures: dict[date, DayTemperature] | None = None,
) -> list[Need]:
    """What the week's plan requires, scaled to the household and rounded up.

    When daily temperatures are supplied, whether an item must be bought on the
    day is decided by the Q10 model rather than by its static label — bread that
    lasts three days in January lasts one in August, and the list should say so.
    """
    start, end = week_bounds(week_start)
    household = get_household(session)
    equivalents = household.adult_equivalents

    meals = list(
        session.exec(
            select(PlannedMeal).where(
                PlannedMeal.plan_date >= start, PlannedMeal.plan_date <= end
            )
        )
    )
    if not meals or equivalents <= 0:
        return []

    dish_ids = {m.dish_id for m in meals}
    dishes = {d.id: d for d in session.exec(select(Dish).where(Dish.id.in_(dish_ids)))}
    ingredients: dict[int, list[DishItem]] = defaultdict(list)
    for row in session.exec(select(DishItem).where(DishItem.dish_id.in_(dish_ids))):
        ingredients[row.dish_id].append(row)

    overrides = {
        o.dish_id: o.factor
        for o in session.exec(select(DishPortion).where(DishPortion.dish_id.in_(dish_ids)))
    }

    item_ids = {row.item_id for rows in ingredients.values() for row in rows}
    items = {i.id: i for i in session.exec(select(Item).where(Item.id.in_(item_ids)))}

    # Perishables are keyed by day; everything else by item alone, so it
    # aggregates across the week.
    raw: dict[tuple[int, date | None], float] = defaultdict(float)
    sources: dict[tuple[int, date | None], list[str]] = defaultdict(list)

    for meal in meals:
        factor = overrides.get(meal.dish_id, 1.0)
        dish = dishes.get(meal.dish_id)
        for row in ingredients.get(meal.dish_id, ()):
            item = items.get(row.item_id)
            if item is None:
                continue
            key = (item.id, meal.plan_date if _buy_daily(item, meal.plan_date, temperatures) else None)
            raw[key] += row.qty_per_adult * equivalents * factor
            if dish is not None and dish.name_en not in sources[key]:
                sources[key].append(dish.name_en)

    needs = [
        Need(
            item=items[item_id],
            quantity=_round_up(total, items[item_id].purchase_step),
            buy_on=buy_on,
            dishes=sources[(item_id, buy_on)],
        )
        for (item_id, buy_on), total in raw.items()
    ]

    # Daily items first, in date order; then the weekly shop.
    return sorted(needs, key=lambda n: (n.buy_on is None, n.buy_on or date.min, n.item.name_en))


def _buy_daily(
    item: Item, on: date, temperatures: dict[date, DayTemperature] | None
) -> bool:
    """Whether this item has to be bought the day it is cooked.

    With no temperature available, the item's static shelf-life label decides —
    which is the honest fallback, since a guess about the weather is worse than
    the seasonal default the label already encodes.
    """
    if temperatures is None:
        return item.shelf_life.buy_daily

    reading = temperatures.get(on)
    if reading is None:
        return item.shelf_life.buy_daily

    return keeps_for(item.keeps_days_at_20c, item.q10, reading.max_c).buy_daily


def keeping_for_item(item: Item, reading: DayTemperature | None):
    """The item's effective shelf life on a given day, for advice and display."""
    if reading is None:
        return keeps_for(item.keeps_days_at_20c, item.q10, REFERENCE_C, True)
    return keeps_for(item.keeps_days_at_20c, item.q10, reading.max_c, reading.estimated)


def _round_up(quantity: float, step: float) -> float:
    if step <= 0:
        return round(quantity, 2)
    return round(math.ceil(quantity / step - 1e-9) * step, 2)


def regenerate(
    session: Session,
    week_start: date,
    temperatures: dict[date, DayTemperature] | None = None,
) -> tuple[int, int]:
    """Rebuild the week's shopping lines from the plan.

    Lines the household has already acted on — purchased, marked unavailable,
    skipped — are left alone. Only pending lines are replaced, so regenerating
    after changing one day's meal never undoes a shop already done.

    Returns (lines created, lines kept).
    """
    start, end = week_bounds(week_start)

    existing = list(
        session.exec(select(ShoppingLine).where(ShoppingLine.week_start == week_start))
    )
    settled = [line for line in existing if line.state is not LineState.PENDING]
    for line in existing:
        if line.state is LineState.PENDING:
            session.delete(line)
    session.flush()

    # What is already accounted for by a settled line does not need buying again.
    covered: dict[tuple[int, date | None], float] = defaultdict(float)
    for line in settled:
        covered[(line.item_id, line.buy_on)] += line.quantity

    created = 0
    for need in compute_needs(session, week_start, temperatures):
        outstanding = need.quantity - covered[(need.item.id, need.buy_on)]
        if outstanding <= 0:
            continue
        session.add(
            ShoppingLine(
                week_start=week_start,
                buy_on=need.buy_on,
                item_id=need.item.id,
                quantity=_round_up(outstanding, need.item.purchase_step),
                unit=need.item.unit,
            )
        )
        created += 1

    session.commit()
    return created, len(settled)


# Roles whose members can stand in for one another at the shop. Deliberately
# narrow: a pulse for a pulse is a fair swap, a pulse for meat is not.
_SUBSTITUTABLE: tuple[ItemRole, ...] = (
    ItemRole.GRAIN,
    ItemRole.PULSE,
    ItemRole.MEAT,
    ItemRole.POULTRY,
    ItemRole.FISH,
    ItemRole.DAIRY,
    ItemRole.VEG_LEAF,
    ItemRole.VEG_FRUIT,
    ItemRole.VEG_ROOT,
    ItemRole.FRUIT,
    ItemRole.FAT,
    ItemRole.SPICE,
    ItemRole.PASTE,
    ItemRole.DRINK,
)


def substitutes(session: Session, item: Item, limit: int = 5) -> list[Item]:
    """Alternatives for an item that was not available at the shop.

    Same role, and for a perishable the substitute must also be one the household
    can use up quickly — suggesting a week's worth of something that will not keep
    solves the wrong problem.
    """
    if item.role not in _SUBSTITUTABLE:
        return []

    candidates = list(
        session.exec(
            select(Item).where(Item.role == item.role, Item.id != item.id, Item.needs_review == False)  # noqa: E712
        )
    )

    if item.shelf_life is not ShelfLife.PERISHABLE:
        # A longer-keeping item can always replace a shorter-keeping one.
        candidates = [c for c in candidates if c.shelf_life is not ShelfLife.PERISHABLE]

    # Prefer things the household has actually bought before.
    bought = {
        line.item_id
        for line in session.exec(
            select(ShoppingLine).where(ShoppingLine.state == LineState.PURCHASED)
        )
    }
    candidates.sort(key=lambda c: (c.id not in bought, c.name_en))
    return candidates[:limit]
