"""The list split when temperature is taken into account.

Same plan, same household, two different weeks of weather — the shop should
differ. This is the behaviour the whole temperature feature exists to produce.
"""

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.kitchen import ensure_kitchen
from app.models import Dish, Item, MealSlot, PlannedMeal
from app.services import shopping
from app.weather import CLIMATE_NORMALS_C, DayTemperature

JANUARY = date(2026, 1, 5)
AUGUST = date(2026, 8, 3)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ensure_kitchen(s)
        yield s


def _plan_week(session: Session, start: date, slug: str, slot=MealSlot.BREAKFAST) -> None:
    dish = session.exec(select(Dish).where(Dish.slug == slug)).first()
    for offset in range(7):
        session.add(
            PlannedMeal(plan_date=start + timedelta(days=offset), slot=slot, dish_id=dish.id)
        )
    session.commit()


def _week_at(start: date, celsius: float) -> dict[date, DayTemperature]:
    return {
        start + timedelta(days=offset): DayTemperature(
            on=start + timedelta(days=offset), max_c=celsius, estimated=False
        )
        for offset in range(7)
    }


def _lines_for(session: Session, start: date, slug: str, temperatures):
    item = session.exec(select(Item).where(Item.slug == slug)).first()
    return [n for n in shopping.compute_needs(session, start, temperatures) if n.item.id == item.id]


def test_in_january_the_weekly_shop_covers_the_first_days_of_bread(session):
    """Bread keeps about four days at 17 C, so it covers part of the week — not all
    of it. Putting a whole week of bread in the weekly shop was a real bug: the
    Saturday loaf would have been bought five days early."""
    _plan_week(session, JANUARY, "zaatar_bread")
    bread = _lines_for(session, JANUARY, "bread", _week_at(JANUARY, CLIMATE_NORMALS_C[1]))

    weekly = [n for n in bread if n.buy_on is None]
    daily = [n for n in bread if n.buy_on is not None]

    assert len(weekly) == 1
    assert daily, "the end of the week has to be bought fresh"
    assert weekly[0].covers_through is not None
    assert weekly[0].covers_through < max(n.buy_on for n in daily)
    # Nothing on the weekly line is held longer than the bread actually keeps.
    assert (weekly[0].covers_through - JANUARY).days <= 4


def test_the_same_bread_is_a_daily_buy_in_august(session):
    _plan_week(session, AUGUST, "zaatar_bread")
    bread = _lines_for(session, AUGUST, "bread", _week_at(AUGUST, CLIMATE_NORMALS_C[8]))

    assert len(bread) == 7, "at 31 C it has to be bought each day"
    assert all(n.buy_on is not None for n in bread)
    assert all(n.covers_through is None for n in bread)


def test_summer_and_winter_produce_different_shops(session):
    _plan_week(session, AUGUST, "zaatar_bread")

    winter = shopping.compute_needs(session, AUGUST, _week_at(AUGUST, 17.0))
    summer = shopping.compute_needs(session, AUGUST, _week_at(AUGUST, 33.0))
    assert len(summer) > len(winter)


def test_buying_daily_does_not_change_the_total_amount_much(session):
    """The split changes when you shop, not how much food you need."""
    _plan_week(session, AUGUST, "zaatar_bread")

    winter = sum(
        n.quantity for n in _lines_for(session, AUGUST, "bread", _week_at(AUGUST, 17.0))
    )
    summer = sum(
        n.quantity for n in _lines_for(session, AUGUST, "bread", _week_at(AUGUST, 33.0))
    )
    # Buying daily rounds up seven times instead of once, so summer is somewhat
    # more — but not several times more.
    assert summer >= winter
    assert summer < winter * 2.5


def test_staples_stay_weekly_however_hot_it_gets(session):
    _plan_week(session, AUGUST, "mujaddara", MealSlot.LUNCH)
    rice = _lines_for(session, AUGUST, "rice", _week_at(AUGUST, 42.0))

    assert len(rice) == 1
    assert rice[0].buy_on is None


def test_meat_stays_daily_however_cold_it_gets(session):
    _plan_week(session, JANUARY, "maqluba_chicken", MealSlot.LUNCH)
    chicken = _lines_for(session, JANUARY, "chicken", _week_at(JANUARY, 12.0))

    assert len(chicken) == 7
    assert all(n.buy_on is not None for n in chicken)


def test_an_item_never_appears_on_both_lists(session):
    """The two-day floor exists for this: without it, something needed on the shop
    day itself landed on the weekly list while its other days were daily."""
    _plan_week(session, JANUARY, "maqluba_chicken", MealSlot.LUNCH)
    needs = shopping.compute_needs(session, JANUARY, _week_at(JANUARY, 12.0))

    daily_items = {n.item.slug for n in needs if n.buy_on is not None}
    weekly_items = {n.item.slug for n in needs if n.buy_on is None}
    assert not daily_items & weekly_items


def test_shelf_life_is_taken_at_the_hottest_day_it_is_held_through(session):
    """Bread bought on a cool Monday for a cool Friday still has to survive a hot
    Wednesday in between."""
    _plan_week(session, JANUARY, "zaatar_bread")

    cool_week = _week_at(JANUARY, 17.0)
    with_hot_day = dict(cool_week)
    midweek = JANUARY + timedelta(days=2)
    with_hot_day[midweek] = DayTemperature(on=midweek, max_c=34.0, estimated=False)

    cool = [n for n in _lines_for(session, JANUARY, "bread", cool_week) if n.buy_on is None]
    hot = [n for n in _lines_for(session, JANUARY, "bread", with_hot_day) if n.buy_on is None]

    assert cool and hot
    # A hot Wednesday does not affect Monday's bread, which is never held through
    # it — but it does stop the weekly shop reaching any further.
    assert hot[0].covers_through < cool[0].covers_through


def test_no_temperature_falls_back_to_the_static_label(session):
    """With the weather off, behaviour is exactly as before the feature existed."""
    _plan_week(session, AUGUST, "zaatar_bread")

    without = shopping.compute_needs(session, AUGUST, None)
    bread = [n for n in without if n.item.slug == "bread"]
    assert len(bread) == 7, "bread's static label is perishable"


def test_a_missing_day_does_not_break_the_week(session):
    """A gap in the forecast falls back for the days it affects, not the whole week."""
    _plan_week(session, AUGUST, "zaatar_bread")
    partial = _week_at(AUGUST, 17.0)
    del partial[AUGUST]

    bread = _lines_for(session, AUGUST, "bread", partial)
    assert bread, "a missing reading must not drop the item from the list"
    assert sum(n.required for n in bread) > 0
    # The day with no reading falls back to the static label, which is perishable.
    assert any(n.buy_on == AUGUST for n in bread)


def test_regenerate_respects_temperature(session):
    _plan_week(session, AUGUST, "zaatar_bread")

    hot, _ = shopping.regenerate(session, AUGUST, _week_at(AUGUST, 33.0))
    cool, _ = shopping.regenerate(session, AUGUST, _week_at(AUGUST, 17.0))
    assert hot > cool
