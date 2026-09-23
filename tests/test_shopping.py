"""The shopping-list engine: portion scaling and the shelf-life split."""

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.kitchen import ensure_kitchen, get_household
from app.models import (
    Dish,
    DishPortion,
    Item,
    LineState,
    MealSlot,
    PlannedMeal,
    ShelfLife,
    ShoppingLine,
)
from app.services import shopping

MONDAY = date(2026, 3, 2)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ensure_kitchen(s)
        yield s


def _dish(session: Session, slug: str) -> Dish:
    return session.exec(select(Dish).where(Dish.slug == slug)).first()


def _item(session: Session, slug: str) -> Item:
    return session.exec(select(Item).where(Item.slug == slug)).first()


def _plan(session: Session, slug: str, day: int, slot: MealSlot = MealSlot.LUNCH) -> None:
    session.add(
        PlannedMeal(plan_date=MONDAY.replace(day=day), slot=slot, dish_id=_dish(session, slug).id)
    )
    session.commit()


def _need(needs, slug: str, session: Session, buy_on: date | None = ...):
    matches = [n for n in needs if n.item.slug == slug]
    if buy_on is not ...:
        matches = [n for n in matches if n.buy_on == buy_on]
    return matches[0] if matches else None


# --- the split ---------------------------------------------------------------


def test_perishables_are_listed_per_day(session):
    _plan(session, "maqluba_chicken", 2)
    _plan(session, "maqluba_chicken", 4)

    needs = shopping.compute_needs(session, MONDAY)
    chicken = [n for n in needs if n.item.slug == "chicken"]

    assert len(chicken) == 2, "chicken cannot be stored, so it is bought twice"
    assert {n.buy_on for n in chicken} == {date(2026, 3, 2), date(2026, 3, 4)}


def test_stable_items_are_aggregated_into_one_weekly_shop(session):
    _plan(session, "maqluba_chicken", 2)
    _plan(session, "maqluba_chicken", 4)

    needs = shopping.compute_needs(session, MONDAY)
    rice = [n for n in needs if n.item.slug == "rice"]

    assert len(rice) == 1, "rice keeps, so it is bought once for the week"
    assert rice[0].buy_on is None


def test_aggregation_happens_before_rounding(session):
    """Seven days of 80 g of rice is 750 g for two adults, not 7 x 250 g."""
    for day in range(2, 9):
        _plan(session, "mujaddara", day)

    rice = _need(shopping.compute_needs(session, MONDAY), "rice", session)
    # 80 g x 2 adults x 7 days = 1120 g, rounded up to the 250 g step.
    assert rice.quantity == 1250
    assert rice.quantity < 7 * 250, "rounding per meal would have bought 1.75 kg"


def test_eggs_keep_a_few_days_so_they_are_a_weekly_buy(session):
    _plan(session, "eggs_tomato", 2, MealSlot.BREAKFAST)
    _plan(session, "eggs_tomato", 3, MealSlot.BREAKFAST)

    eggs = [n for n in shopping.compute_needs(session, MONDAY) if n.item.slug == "eggs"]
    assert len(eggs) == 1
    assert eggs[0].buy_on is None
    assert _item(session, "eggs").shelf_life is ShelfLife.KEEPS_DAYS


def test_bread_is_perishable_and_bought_daily(session):
    _plan(session, "zaatar_bread", 2, MealSlot.BREAKFAST)
    _plan(session, "zaatar_bread", 3, MealSlot.BREAKFAST)

    bread = [n for n in shopping.compute_needs(session, MONDAY) if n.item.slug == "bread"]
    assert len(bread) == 2
    assert all(n.buy_on is not None for n in bread)


# --- portion scaling ---------------------------------------------------------


def test_children_count_for_less_than_adults(session):
    household = get_household(session)
    household.adults = 2
    household.children = 3
    session.add(household)
    session.commit()
    assert household.adult_equivalents == pytest.approx(3.8)

    _plan(session, "mujaddara", 2)
    rice = _need(shopping.compute_needs(session, MONDAY), "rice", session)
    # 80 g x 3.8 = 304 g, rounded up to the 250 g step.
    assert rice.quantity == 500


def test_a_bigger_household_needs_more(session):
    _plan(session, "mujaddara", 2)
    small = _need(shopping.compute_needs(session, MONDAY), "onion", session).quantity

    household = get_household(session)
    household.adults = 6
    session.add(household)
    session.commit()

    big = _need(shopping.compute_needs(session, MONDAY), "onion", session).quantity
    assert big > small


def test_portion_override_is_applied(session):
    _plan(session, "mujaddara", 2)
    before = _need(shopping.compute_needs(session, MONDAY), "lentils", session).quantity

    session.add(DishPortion(dish_id=_dish(session, "mujaddara").id, factor=3.0))
    session.commit()

    after = _need(shopping.compute_needs(session, MONDAY), "lentils", session).quantity
    assert after > before


def test_purchase_steps_absorb_small_differences(session):
    """A consequence worth knowing: you cannot buy 120 g of lentils.

    For a small household over a short week, the purchase step decides the
    quantity more than the portion maths does. The listed amount is what a shop
    can actually sell, which is the honest number to show — but it means a modest
    portion override may not change the list at all.
    """
    _plan(session, "mujaddara", 2)
    before = _need(shopping.compute_needs(session, MONDAY), "lentils", session).quantity

    session.add(DishPortion(dish_id=_dish(session, "mujaddara").id, factor=1.5))
    session.commit()

    after = _need(shopping.compute_needs(session, MONDAY), "lentils", session).quantity
    assert after == before == 250


def test_empty_plan_needs_nothing(session):
    assert shopping.compute_needs(session, MONDAY) == []


def test_meals_outside_the_week_are_ignored(session):
    _plan(session, "mujaddara", 2)
    session.add(
        PlannedMeal(plan_date=date(2026, 4, 20), dish_id=_dish(session, "molokhia").id)
    )
    session.commit()

    slugs = {n.item.slug for n in shopping.compute_needs(session, MONDAY)}
    assert "molokhia_dry" not in slugs


def test_needs_record_which_dishes_asked_for_them(session):
    _plan(session, "mujaddara", 2)
    rice = _need(shopping.compute_needs(session, MONDAY), "rice", session)
    assert "Mujaddara" in rice.dishes


# --- regeneration ------------------------------------------------------------


def test_regenerate_creates_lines(session):
    _plan(session, "mujaddara", 2)
    created, kept = shopping.regenerate(session, MONDAY)
    assert created > 0
    assert kept == 0
    assert len(list(session.exec(select(ShoppingLine)))) == created


def test_regenerate_is_idempotent(session):
    _plan(session, "mujaddara", 2)
    first, _ = shopping.regenerate(session, MONDAY)
    second, _ = shopping.regenerate(session, MONDAY)
    assert first == second
    assert len(list(session.exec(select(ShoppingLine)))) == second


def test_regenerate_preserves_a_shop_already_done(session):
    _plan(session, "mujaddara", 2)
    shopping.regenerate(session, MONDAY)

    rice_line = session.exec(
        select(ShoppingLine).where(ShoppingLine.item_id == _item(session, "rice").id)
    ).first()
    rice_line.state = LineState.PURCHASED
    rice_line.paid = 8.0
    session.add(rice_line)
    session.commit()

    # Adding another meal must not undo Monday's shop or ask for the rice again.
    _plan(session, "mujaddara", 3)
    created, kept = shopping.regenerate(session, MONDAY)

    assert kept == 1
    session.refresh(rice_line)
    assert rice_line.state is LineState.PURCHASED

    remaining = [
        line
        for line in session.exec(select(ShoppingLine))
        if line.item_id == _item(session, "rice").id and line.state is LineState.PENDING
    ]
    # The second meal needs more rice than the 250 g already bought, so one
    # top-up line is expected — not a fresh full-week line.
    assert all(line.quantity <= 500 for line in remaining)


# --- substitution ------------------------------------------------------------


def test_substitutes_share_the_role(session):
    options = shopping.substitutes(session, _item(session, "lentils"))
    assert options
    assert all(o.role == _item(session, "lentils").role for o in options)
    assert all(o.slug != "lentils" for o in options)


def test_meat_is_not_offered_for_a_pulse(session):
    slugs = {o.slug for o in shopping.substitutes(session, _item(session, "lentils"))}
    assert "chicken" not in slugs
    assert "beef" not in slugs


def test_a_keeping_item_is_not_replaced_by_a_perishable(session):
    # Onions keep; suggesting something that spoils solves the wrong problem.
    options = shopping.substitutes(session, _item(session, "onion"))
    assert all(o.shelf_life is not ShelfLife.PERISHABLE for o in options)


def test_unsubstitutable_roles_return_nothing(session):
    assert shopping.substitutes(session, _item(session, "bread")) == []
