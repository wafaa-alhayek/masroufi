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
from app.kitchen.spoilage import DAILY_THRESHOLD_DAYS, REFERENCE_C, keeps_for
from app.services import pantry
from app.weather import DayTemperature
from app.models import (
    Dish,
    DishItem,
    DishPortion,
    Item,
    ItemFeeling,
    ItemPreference,
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
    required: float = 0.0
    from_stock: float = 0.0
    covers_through: date | None = None

    @property
    def fully_covered(self) -> bool:
        """The cupboard already has all of this, so it costs nothing."""
        return self.quantity <= 0 and self.required > 0


def week_bounds(week_start: date) -> tuple[date, date]:
    return week_start, week_start + timedelta(days=6)


def compute_needs(
    session: Session,
    week_start: date,
    temperatures: dict[date, DayTemperature] | None = None,
    use_stock: bool = True,
) -> list[Need]:
    """What the week's plan requires, scaled to the household and rounded up.

    When daily temperatures are supplied, whether an item must be bought on the
    day is decided by the Q10 model rather than by its static label — bread that
    lasts three days in January lasts one in August, and the list should say so.

    What the household already has is subtracted before anything is asked for.
    A household holding months of aid staples should never be told to buy more
    lentils, and each Need reports `required`, `from_stock` and the `quantity`
    still to buy so the list can show why a line is small or absent.
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
    # The last day a weekly line is expected to cover. An item that keeps four
    # days can legitimately have both a weekly line for the first half of the week
    # and daily lines for the rest, and the UI has to be able to say so.
    spans: dict[int, date] = {}

    for meal in meals:
        factor = overrides.get(meal.dish_id, 1.0)
        dish = dishes.get(meal.dish_id)
        for row in ingredients.get(meal.dish_id, ()):
            item = items.get(row.item_id)
            if item is None:
                continue
            daily = _buy_daily(item, meal.plan_date, week_start, temperatures)
            key = (item.id, meal.plan_date if daily else None)
            if not daily:
                current = spans.get(item.id)
                if current is None or meal.plan_date > current:
                    spans[item.id] = meal.plan_date
            raw[key] += row.qty_per_adult * equivalents * factor
            if dish is not None and dish.name_en not in sources[key]:
                sources[key].append(dish.name_en)

    on_hand = pantry.available(session, set(items)) if use_stock else {}

    needs: list[Need] = []
    # Perishables are drawn against first, in date order, so the cupboard covers
    # the earliest meal rather than an arbitrary one.
    for (item_id, buy_on), total in sorted(
        raw.items(), key=lambda kv: (kv[0][1] is None, kv[0][1] or date.min)
    ):
        item = items[item_id]
        covered = min(on_hand.get(item_id, 0.0), total)
        if covered > 0:
            on_hand[item_id] = round(on_hand[item_id] - covered, 4)

        outstanding = total - covered
        needs.append(
            Need(
                item=item,
                quantity=_round_up(outstanding, item.purchase_step) if outstanding > 0 else 0.0,
                buy_on=buy_on,
                dishes=sources[(item_id, buy_on)],
                required=round(total, 2),
                from_stock=round(covered, 2),
                covers_through=spans.get(item_id) if buy_on is None else None,
            )
        )

    # Daily items first, in date order; then the weekly shop.
    return sorted(needs, key=lambda n: (n.buy_on is None, n.buy_on or date.min, n.item.name_en))


def _buy_daily(
    item: Item,
    needed_on: date,
    shop_on: date,
    temperatures: dict[date, DayTemperature] | None,
) -> bool:
    """Whether this item has to be bought near the day it is cooked.

    The question is not "does this keep for a while" but "will this survive from
    the weekly shop until the day it is cooked", which are different and were
    conflated in the first version of this. Bread keeping four days is fine for
    Tuesday's meal and useless for Saturday's, so the comparison is the item's
    effective shelf life against the number of days it would have to be held.

    Shelf life is taken at the **hottest day it would be stored through**, not the
    day it is eaten: bread bought on a cool Monday for a cool Friday still has to
    survive a hot Wednesday in between.
    """
    days_to_hold = max((needed_on - shop_on).days, 0)

    keeps = _keeps_through(item, shop_on, needed_on, temperatures)
    if keeps is None:
        # No usable temperature. The static label decides for anything that does
        # not keep at all, and the reference shelf life decides the rest — a
        # coarse label should not let a five-day item sit for six.
        if item.shelf_life.buy_daily:
            return True
        return item.keeps_days_at_20c <= days_to_hold

    if keeps.days < DAILY_THRESHOLD_DAYS:
        # Cannot be held even a couple of days, so it is a fresh item however the
        # week falls. Without this floor, something needed on the shop day itself
        # would land on the weekly list and the same item could appear on both.
        return True
    return keeps.days <= days_to_hold


def _keeps_through(
    item: Item,
    shop_on: date,
    needed_on: date,
    temperatures: dict[date, DayTemperature] | None,
):
    """Effective shelf life across the whole holding window, worst day first."""
    if not temperatures:
        return None

    readings = [
        temperatures[day]
        for day in _days_between(shop_on, needed_on)
        if day in temperatures
    ]
    if not readings:
        return None

    hottest = max(readings, key=lambda r: r.max_c)
    return keeps_for(item.keeps_days_at_20c, item.q10, hottest.max_c, hottest.estimated)


def _days_between(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


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
    # A FROM_STOCK line the app created itself is a derived row, not an answer
    # the household gave, so it is rebuilt like a pending one.
    rebuildable = {LineState.PENDING, LineState.FROM_STOCK}
    settled = [line for line in existing if line.state not in rebuildable]
    for line in existing:
        if line.state in rebuildable and line.paid is None:
            session.delete(line)
    session.flush()

    # What is already accounted for by a settled line does not need buying again.
    covered: dict[tuple[int, date | None], float] = defaultdict(float)
    for line in settled:
        covered[(line.item_id, line.buy_on)] += line.quantity

    created = 0
    for need in compute_needs(session, week_start, temperatures):
        if need.quantity <= 0:
            # Already in the cupboard. Recorded as a line anyway, in the
            # FROM_STOCK state, so the household can see that the app knew it had
            # this — and can correct it if the stock record is wrong. A silently
            # absent row is indistinguishable from a bug.
            if need.required > 0 and covered[(need.item.id, need.buy_on)] <= 0:
                session.add(
                    ShoppingLine(
                        week_start=week_start,
                        buy_on=need.buy_on,
                        item_id=need.item.id,
                        quantity=round(need.required, 2),
                        unit=need.item.unit,
                        required=round(need.required, 2),
                        from_stock=round(need.from_stock, 2),
                        covers_through=need.covers_through,
                        state=LineState.FROM_STOCK,
                    )
                )
                created += 1
            continue
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
                required=round(need.required, 2),
                from_stock=round(need.from_stock, 2),
                covers_through=need.covers_through,
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

    Anything the household has said it has had enough of is never offered. Being
    handed lentils as a substitute is exactly the nudge the weariness setting
    exists to prevent, and it would be worse here than in a suggestion list because
    it arrives at the moment someone is already having to improvise.
    """
    if item.role not in _SUBSTITUTABLE:
        return []

    weary = {
        p.item_id
        for p in session.exec(select(ItemPreference))
        if p.feeling is ItemFeeling.WEARY
    }
    candidates = [
        candidate
        for candidate in session.exec(
            select(Item).where(Item.role == item.role, Item.id != item.id, Item.needs_review == False)  # noqa: E712
        )
        if candidate.id not in weary
    ]

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
