"""The cupboard, and the shopping list that subtracts it.

The scenario throughout is the real one: a household holding months of aid
lentils and rice, being asked by a naive planner to buy more lentils.
"""

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.kitchen import ensure_kitchen, get_household
from app.models import (
    Dish,
    Item,
    ItemFeeling,
    ItemPreference,
    MealSlot,
    PantryStock,
    PlannedMeal,
    StockCondition,
    StockSource,
)
from app.services import pantry, shopping, suggest

MONDAY = date(2026, 3, 2)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ensure_kitchen(s)
        get_household(s)
        yield s


def _item(session: Session, slug: str) -> Item:
    return session.exec(select(Item).where(Item.slug == slug)).first()


def _dish(session: Session, slug: str) -> Dish:
    return session.exec(select(Dish).where(Dish.slug == slug)).first()


def _stock(session: Session, slug: str, qty: float, days_ago: int = 0, **kw) -> PantryStock:
    row = pantry.add(
        session,
        _item(session, slug),
        qty,
        kw.pop("source", StockSource.AID),
        acquired_on=MONDAY - timedelta(days=days_ago),
        **kw,
    )
    session.commit()
    return row


def _plan(session: Session, slug: str, day: int, slot=MealSlot.LUNCH) -> None:
    session.add(
        PlannedMeal(plan_date=MONDAY.replace(day=day), slot=slot, dish_id=_dish(session, slug).id)
    )
    session.commit()


def _need(session: Session, slug: str, temperatures=None):
    item = _item(session, slug)
    rows = [n for n in shopping.compute_needs(session, MONDAY, temperatures) if n.item.id == item.id]
    return rows[0] if rows else None


# --- stock covers the list ---------------------------------------------------


def test_the_aid_lentils_case(session):
    """A household given 5 kg of lentils is not asked to buy lentils."""
    _stock(session, "lentils", 5000)
    _plan(session, "mujaddara", 2)

    lentils = _need(session, "lentils")
    assert lentils.required > 0
    assert lentils.from_stock == lentils.required
    assert lentils.quantity == 0
    assert lentils.fully_covered


def test_partial_stock_asks_only_for_the_difference(session):
    # A week of it, so the difference is bigger than one 250 g bag — otherwise the
    # purchase step absorbs the saving, which is correct but not what is tested here.
    for day in range(2, 9):
        _plan(session, "mujaddara", day)
    full = _need(session, "rice").quantity

    _stock(session, "rice", 500)
    reduced = _need(session, "rice")
    assert reduced.from_stock == 500
    assert reduced.quantity < full


def test_covered_lines_are_not_created_at_all(session):
    _stock(session, "lentils", 5000)
    _stock(session, "rice", 5000)
    _plan(session, "mujaddara", 2)

    shopping.regenerate(session, MONDAY)
    listed = {
        session.get(Item, line.item_id).slug
        for line in session.exec(select(shopping.ShoppingLine))
    }
    assert "lentils" not in listed
    assert "rice" not in listed
    assert "onion" in listed, "what is not in the cupboard is still asked for"


def test_spoiled_stock_does_not_count(session):
    """The one failure that costs someone a meal, so it is tested directly."""
    row = _stock(session, "lentils", 5000)
    row.condition = StockCondition.SPOILED
    session.add(row)
    session.commit()

    _plan(session, "mujaddara", 2)
    assert _need(session, "lentils").quantity > 0


def test_stock_is_shared_across_the_week_not_reused_per_meal(session):
    _stock(session, "lentils", 100)
    _plan(session, "mujaddara", 2)
    _plan(session, "mujaddara", 3)

    lentils = _need(session, "lentils")
    # Lentils keep, so this is one weekly line; 100 g covers part of the total,
    # not 100 g against each meal.
    assert lentils.from_stock == 100
    assert lentils.required > 100


def test_perishable_stock_covers_the_earliest_day_first(session):
    # Exactly one day of bread, so the second day must still be bought.
    _stock(session, "bread", 240)
    _plan(session, "zaatar_bread", 2, MealSlot.BREAKFAST)
    _plan(session, "zaatar_bread", 3, MealSlot.BREAKFAST)

    bread = sorted(
        (n for n in shopping.compute_needs(session, MONDAY) if n.item.slug == "bread"),
        key=lambda n: n.buy_on,
    )
    assert bread[0].from_stock > 0
    assert bread[1].from_stock == 0


def test_an_empty_cupboard_changes_nothing(session):
    _plan(session, "mujaddara", 2)
    with_stock = shopping.compute_needs(session, MONDAY, use_stock=True)
    without = shopping.compute_needs(session, MONDAY, use_stock=False)
    assert [n.quantity for n in with_stock] == [n.quantity for n in without]


# --- spending stock ----------------------------------------------------------


def test_consume_takes_from_stock(session):
    _stock(session, "rice", 1000)
    assert pantry.consume(session, _item(session, "rice").id, 400) == 400
    session.commit()
    assert pantry.available(session)[_item(session, "rice").id] == 600


def test_consume_cannot_take_more_than_is_there(session):
    _stock(session, "rice", 100)
    assert pantry.consume(session, _item(session, "rice").id, 500) == 100


def test_consume_spends_the_oldest_first(session):
    old = _stock(session, "rice", 500, days_ago=200)
    new = _stock(session, "rice", 500, days_ago=2)

    pantry.consume(session, _item(session, "rice").id, 500)
    session.commit()
    session.refresh(old)
    session.refresh(new)
    assert old.quantity == 0
    assert new.quantity == 500


def test_consume_prefers_at_risk_stock_over_good(session):
    good = _stock(session, "rice", 500, days_ago=300)
    risky = _stock(session, "rice", 500, days_ago=1)
    risky.condition = StockCondition.AT_RISK
    session.add(risky)
    session.commit()

    pantry.consume(session, _item(session, "rice").id, 500)
    session.commit()
    session.refresh(good)
    session.refresh(risky)
    assert risky.quantity == 0, "use what is doubtful before what is sound"
    assert good.quantity == 500


def test_consume_skips_spoiled_stock(session):
    row = _stock(session, "rice", 500)
    row.condition = StockCondition.SPOILED
    session.add(row)
    session.commit()
    assert pantry.consume(session, _item(session, "rice").id, 100) == 0


# --- the flour and moths case ------------------------------------------------


def test_long_stored_flour_is_flagged(session):
    _stock(session, "flour", 10000, days_ago=200)
    risks = {r.name_en: r for r in pantry.assess(session, on=MONDAY, celsius=30.0)}

    flour = risks["Wheat flour"]
    assert flour.fraction_used > 1.0, "200 days at 30 C is past what flour keeps"
    assert flour.note is not None
    assert "200 days" in flour.note


def test_the_same_flour_is_fine_when_stored_cool(session):
    _stock(session, "flour", 10000, days_ago=200)
    cool = {r.name_en: r for r in pantry.assess(session, on=MONDAY, celsius=15.0)}
    assert cool["Wheat flour"].fraction_used < 1.0


def test_fresh_stock_gets_no_warning(session):
    _stock(session, "rice", 5000, days_ago=3)
    assert all(r.note is None for r in pantry.assess(session, on=MONDAY, celsius=30.0))


def test_assessment_never_marks_anything_spoiled_itself(session):
    _stock(session, "flour", 10000, days_ago=400)
    pantry.assess(session, on=MONDAY, celsius=35.0)
    session.commit()
    row = session.exec(select(PantryStock)).first()
    assert row.condition is StockCondition.GOOD, "only the household decides this"


def test_at_risk_stock_still_counts_as_stock(session):
    row = _stock(session, "lentils", 5000)
    row.condition = StockCondition.AT_RISK
    session.add(row)
    session.commit()
    _plan(session, "mujaddara", 2)
    assert _need(session, "lentils").quantity == 0


# --- suggestions -------------------------------------------------------------


def test_suggestions_prefer_what_is_in_the_cupboard(session):
    _stock(session, "lentils", 5000)
    _stock(session, "rice", 5000)
    _stock(session, "onion", 2000)
    _stock(session, "veg_oil", 1000)
    _stock(session, "cumin", 200)
    _stock(session, "salt", 500)

    top = suggest.suggest(session, on=MONDAY, slot=MealSlot.LUNCH)[0]
    assert top.name_en == "Mujaddara"
    assert top.stock_cover == 1.0
    assert top.missing == []


def test_weariness_pushes_a_dish_down_without_removing_it(session):
    for slug, qty in (("lentils", 5000), ("rice", 5000), ("onion", 2000), ("veg_oil", 1000), ("cumin", 200), ("salt", 500)):
        _stock(session, slug, qty)

    before = suggest.suggest(session, on=MONDAY, slot=MealSlot.LUNCH)
    assert before[0].name_en == "Mujaddara"

    session.add(ItemPreference(item_id=_item(session, "lentils").id, feeling=ItemFeeling.WEARY))
    session.commit()

    ranked = suggest.suggest(session, on=MONDAY, slot=MealSlot.LUNCH, limit=50)
    after = {s.name_en: s for s in ranked}
    assert after["Mujaddara"].score < before[0].score
    assert "Lentils" in after["Mujaddara"].weary_items
    assert "enough of" in after["Mujaddara"].why
    assert "Mujaddara" in after, "still offered, never hidden"

    names = [s.name_en for s in ranked]
    assert names[0] != "Mujaddara", "a weary dish is never the top suggestion"


def test_a_weary_base_gets_no_credit_for_a_cheap_spice(session):
    """"A different meal for the price of a spice" is meaningless if the base is
    the thing someone cannot face again."""
    for slug, qty in (
        ("lentils", 5000), ("rice", 5000), ("carrot", 2000), ("potato", 3000),
        ("onion", 2000), ("veg_oil", 1000), ("salt", 500),
    ):
        _stock(session, slug, qty)

    name = "Lentil and vegetable stew"
    before = {s.name_en: s for s in suggest.suggest(session, on=MONDAY, limit=50)}[name]
    assert before.flavour_only

    session.add(ItemPreference(item_id=_item(session, "lentils").id, feeling=ItemFeeling.WEARY))
    session.commit()

    after = {s.name_en: s for s in suggest.suggest(session, on=MONDAY, limit=50)}[name]
    # Still factually true that only a paste is missing, but it earns no bonus.
    assert after.flavour_only
    assert after.score < before.score - 0.3
    assert "price of a spice" not in after.why


def test_the_explanation_never_argues_back(session):
    _stock(session, "lentils", 5000)
    session.add(ItemPreference(item_id=_item(session, "lentils").id, feeling=ItemFeeling.WEARY))
    session.commit()

    why = {s.name_en: s.why for s in suggest.suggest(session, on=MONDAY, limit=50)}["Mujaddara"]
    for nudge in ("cheap", "should", "try", "healthy", "affordable"):
        assert nudge not in why.lower()


def test_repetition_is_counted_from_the_plan(session):
    for day in range(2, 9):
        _plan(session, "mujaddara", day)

    found = {s.name_en: s for s in suggest.suggest(session, on=MONDAY.replace(day=9))}
    assert found["Mujaddara"].repetition >= 4
    assert "last 14 days" in found["Mujaddara"].why


def test_spices_do_not_count_as_repetition(session):
    """Salt is in almost everything; it must not make every dish a repeat."""
    for day in range(2, 9):
        _plan(session, "mujaddara", day)

    found = {s.name_en: s for s in suggest.suggest(session, on=MONDAY.replace(day=9), limit=50)}
    # Shakshuka shares onions, oil and salt with mujaddara, but shares no base:
    # mujaddara is rice and lentils, shakshuka is eggs and bread. Nobody gets
    # tired of onions.
    assert found["Shakshuka"].repetition == 0
    assert found["Mujaddara"].repetition >= 4


def test_the_flavour_only_insight(session):
    """The honest answer to lentil fatigue: a different meal for the price of a spice."""
    for slug, qty in (
        ("lentils", 5000),
        ("rice", 5000),
        ("carrot", 2000),
        ("potato", 3000),
        ("onion", 2000),
        ("veg_oil", 1000),
        ("salt", 500),
    ):
        _stock(session, slug, qty)

    stew = {s.name_en: s for s in suggest.suggest(session, on=MONDAY)}[
        "Lentil and vegetable stew"
    ]
    assert stew.flavour_only, "only the tomato paste is missing"
    assert {m.name_en for m in stew.missing} == {"Tomato paste"}
    assert "price of a spice" in stew.why


def test_flavour_only_beats_an_equally_stocked_repeat(session):
    for slug, qty in (
        ("lentils", 5000), ("rice", 5000), ("carrot", 2000), ("potato", 3000),
        ("onion", 2000), ("veg_oil", 1000), ("salt", 500), ("cumin", 200),
    ):
        _stock(session, slug, qty)
    for day in range(2, 9):
        _plan(session, "mujaddara", day)

    ranked = suggest.suggest(session, on=MONDAY.replace(day=9), slot=MealSlot.LUNCH)
    names = [s.name_en for s in ranked]
    assert names.index("Lentil and vegetable stew") < names.index("Mujaddara")


def test_a_dish_needing_nothing_and_no_gas_is_called_out(session):
    _stock(session, "bread", 2000)
    _stock(session, "zaatar", 300)
    _stock(session, "olive_oil", 500)

    found = {s.name_en: s for s in suggest.suggest(session, on=MONDAY, slot=MealSlot.BREAKFAST)}
    zaatar = found["Bread with zaatar and oil"]
    assert zaatar.missing == []
    assert zaatar.gas_kg == 0.0
    assert "no cooking gas" in zaatar.why


def test_empty_cupboard_still_returns_suggestions(session):
    out = suggest.suggest(session, on=MONDAY)
    assert out
    assert all(s.stock_cover == 0.0 for s in out)
    assert all(s.missing for s in out)
