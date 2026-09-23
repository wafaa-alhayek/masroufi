"""Which dish to cook next, given what is in the cupboard and what people are
sick of.

This exists because of a real problem the rest of the app made worse. A household
holding months of aid lentils, rice and flour gets told by a naive planner to cook
lentils, because lentils are what it has and what it can afford. That is also
exactly what everyone has been eating for months, and a plan nobody will follow is
worth nothing.

What this can honestly do:

  * Stop asking for what is already in the cupboard.
  * Rank down what has been eaten recently, and what the household has said it is
    weary of, without ever asking why or nudging back.
  * Point at the cheapest change. This is the useful part: you cannot conjure a new
    protein out of a cupboard of pulses, but the same pulses in a different dish,
    with a spice or a paste costing a few shekels, is a genuinely different meal.
    The app can say precisely which few shekels.

What it cannot do, and does not pretend to: make a household that only has lentils
have something other than lentils.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlmodel import Session, select

from app.kitchen import fuel
from app.models import (
    Dish,
    DishItem,
    Household,
    Item,
    ItemFeeling,
    ItemPreference,
    ItemRole,
    MealSlot,
    PlannedMeal,
    Unit,
)
from app.services import pantry, prices

# How far back "recently eaten" looks.
RECENT_DAYS = 14

# Roles that change how a meal tastes without changing what it is made of. When
# the only thing missing from a dish is one of these, a different meal is a very
# small purchase away — which is the whole point of this module.
FLAVOUR_ROLES = frozenset({ItemRole.SPICE, ItemRole.PASTE, ItemRole.FAT})

# What makes two meals feel like the same meal is their base, not their
# aromatics. Nobody tires of onions; people tire of lentils and bread — which is
# exactly what a household eating aid rations for months reports. So repetition
# and weariness are judged on these roles only, and onion, garlic, oil and spice
# are treated as background however often they appear.
SIGNATURE_ROLES = frozenset(
    {
        ItemRole.GRAIN,
        ItemRole.PULSE,
        ItemRole.BREAD,
        ItemRole.MEAT,
        ItemRole.POULTRY,
        ItemRole.FISH,
        ItemRole.EGG,
        ItemRole.DAIRY,
    }
)

# A garnish is not a base. Applied to weights only — an item counted in pieces or
# bunches (two eggs, one bunch of parsley) is never a trivial amount.
SIGNATURE_MIN_WEIGHT = 20.0


@dataclass
class Missing:
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    role: str
    cost: float | None = None


@dataclass
class Suggestion:
    dish_id: int
    name_en: str
    name_ar: str
    slot: MealSlot
    stock_cover: float
    missing: list[Missing] = field(default_factory=list)
    repetition: int = 0
    weary_items: list[str] = field(default_factory=list)
    gas_kg: float = 0.0
    missing_cost: float | None = None
    missing_cost_partial: bool = False
    flavour_only: bool = False
    score: float = 0.0
    why: str = ""

    @property
    def weary(self) -> bool:
        return bool(self.weary_items)


def suggest(
    session: Session,
    on: date | None = None,
    slot: MealSlot | None = None,
    limit: int = 8,
    include_weary: bool = False,
) -> tuple[list[Suggestion], list[Suggestion]]:
    """Returns (offered, withheld).

    A dish built on something the household has had enough of is withheld, not
    ranked lower. It is returned separately rather than dropped so a screen can say
    how many are hidden and why, and offer to show them — an unexplained short list
    looks like a broken app, and hiding the reason is its own kind of nudge.
    """
    today = on or date.today()
    household = session.exec(select(Household)).first()
    equivalents = household.adult_equivalents if household else 1.0

    items = {i.id: i for i in session.exec(select(Item))}
    on_hand = pantry.available(session, set(items))
    weary = {
        p.item_id
        for p in session.exec(select(ItemPreference))
        if p.feeling is ItemFeeling.WEARY
    }
    recent = _recent_item_counts(session, today, items)
    known_prices = prices.estimates(session, items, on=today)

    statement = select(Dish)
    if slot is not None:
        statement = statement.where(Dish.default_slot == slot)
    dishes = list(session.exec(statement))

    ingredients: dict[int, list[DishItem]] = {}
    for row in session.exec(select(DishItem)):
        ingredients.setdefault(row.dish_id, []).append(row)

    out: list[Suggestion] = []
    for dish in dishes:
        rows = ingredients.get(dish.id, [])
        if not rows:
            continue
        out.append(
            _score_dish(
                dish, rows, items, on_hand, weary, recent, equivalents, known_prices
            )
        )

    out.sort(key=lambda s: s.score, reverse=True)

    offered = [s for s in out if not s.weary]
    withheld = [s for s in out if s.weary]
    if include_weary:
        # Explicitly asked for, e.g. the household tapped "show them anyway".
        offered = offered + withheld
        withheld = []
    return offered[:limit], withheld


def _recent_item_counts(
    session: Session, today: date, items: dict[int, Item]
) -> dict[int, int]:
    """How many times each item has been on the menu lately.

    Counts signature ingredients only, so a dish is not judged a repeat because it
    also used salt.
    """
    since = today - timedelta(days=RECENT_DAYS)
    meals = list(
        session.exec(
            select(PlannedMeal).where(
                PlannedMeal.plan_date >= since, PlannedMeal.plan_date <= today
            )
        )
    )
    if not meals:
        return {}

    dish_ids = {m.dish_id for m in meals}
    by_dish: dict[int, list[DishItem]] = {}
    for row in session.exec(select(DishItem).where(DishItem.dish_id.in_(dish_ids))):
        by_dish.setdefault(row.dish_id, []).append(row)

    counts: dict[int, int] = {}
    for meal in meals:
        for row in by_dish.get(meal.dish_id, []):
            item = items.get(row.item_id)
            if item is None or not _is_signature(item, row):
                continue
            counts[row.item_id] = counts.get(row.item_id, 0) + 1
    return counts


def _is_signature(item: Item, row: DishItem) -> bool:
    """Whether this ingredient is what the meal *is*, rather than how it tastes."""
    if item.role not in SIGNATURE_ROLES:
        return False
    if item.unit in (Unit.GRAM, Unit.ML):
        return row.qty_per_adult >= SIGNATURE_MIN_WEIGHT
    return True


def _score_dish(
    dish: Dish,
    rows: list[DishItem],
    items: dict[int, Item],
    on_hand: dict[int, float],
    weary: set[int],
    recent: dict[int, int],
    equivalents: float,
    known_prices: dict,
) -> Suggestion:
    required_total = covered_total = 0.0
    missing: list[Missing] = []
    weary_names: list[str] = []
    repetition = 0

    for row in rows:
        item = items.get(row.item_id)
        if item is None:
            continue

        needed = row.qty_per_adult * equivalents
        held = on_hand.get(item.id, 0.0)
        covered = min(held, needed)

        required_total += needed
        covered_total += covered

        if needed - covered > 0.01:
            shortfall = needed - covered
            est = known_prices.get(item.id)
            missing.append(
                Missing(
                    item_id=item.id,
                    name_en=item.name_en,
                    name_ar=item.name_ar,
                    quantity=round(shortfall, 2),
                    unit=item.unit.value,
                    role=item.role.value,
                    cost=round(est.per_unit * shortfall, 2) if est else None,
                )
            )

        if _is_signature(item, row):
            repetition = max(repetition, recent.get(item.id, 0))
            if item.id in weary:
                weary_names.append(item.name_en)

    cover = round(covered_total / required_total, 3) if required_total else 0.0
    gas = fuel.estimate(dish.full_flame_minutes, dish.simmer_minutes, dish.burners).kg

    # Only add up what is actually known. A total that silently omits half the
    # missing items is worse than one that says it is partial.
    priced = [m.cost for m in missing if m.cost is not None]
    missing_cost = round(sum(priced), 2) if priced else None
    missing_cost_partial = bool(missing) and len(priced) != len(missing)
    flavour_only = bool(missing) and all(
        items[m.item_id].role in FLAVOUR_ROLES for m in missing
    )

    # An explicit, explainable score rather than a tuned black box: cover is good,
    # repetition and weariness are bad, gas is a mild cost. Every term is reported
    # alongside the total so a screen can justify the order it shows.
    score = (
        cover * 1.0
        - min(repetition, 7) / 7 * 0.8
        - (0.6 if weary_names else 0.0)
        - min(gas, 0.35) / 0.35 * 0.25
    )
    if flavour_only and not weary_names:
        # A different meal from the same cupboard for the price of a spice. Not
        # offered when the base is the thing they are tired of — swapping the spice
        # does not make it a different meal in the way that matters.
        score += 0.35

    return Suggestion(
        dish_id=dish.id,
        name_en=dish.name_en,
        name_ar=dish.name_ar,
        slot=dish.default_slot,
        stock_cover=cover,
        missing=missing,
        repetition=repetition,
        weary_items=weary_names,
        gas_kg=gas,
        missing_cost=missing_cost,
        missing_cost_partial=missing_cost_partial,
        flavour_only=flavour_only,
        score=round(score, 3),
        why=_explain(
            cover, missing, repetition, weary_names, flavour_only, gas, missing_cost,
            missing_cost_partial,
        ),
    )


def _explain(
    cover: float,
    missing: list[Missing],
    repetition: int,
    weary: list[str],
    flavour_only: bool,
    gas: float,
    missing_cost: float | None = None,
    partial: bool = False,
) -> str:
    """One honest line. No encouragement, no nudging back towards what someone
    has said they are tired of."""
    if weary:
        return f"Uses {', '.join(weary)}, which you have had enough of."
    if flavour_only:
        names = ", ".join(m.name_en for m in missing)
        if missing_cost is not None and not partial:
            return (
                f"Everything but {names} is already in your cupboard — a different "
                f"meal for about {missing_cost:g}."
            )
        return (
            f"Everything but {names} is already in your cupboard — a different meal "
            "for the price of a spice."
        )
    if not missing:
        if gas == 0:
            return "Fully covered by your cupboard, and needs no cooking gas."
        return "Fully covered by your cupboard."
    if repetition >= 4:
        return f"You have had this most of the last {RECENT_DAYS} days."
    if cover >= 0.6:
        return f"Mostly in your cupboard; {len(missing)} thing(s) to buy."
    return f"{len(missing)} thing(s) to buy."
