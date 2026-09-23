"""Prices as dated observations, never as stored facts.

The market these serve has seen recorded food prices move by tens of percent
inside a month, so the behaviour under test is mostly about *not* asserting a
number more confidently than the data supports.
"""

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.kitchen import ensure_kitchen
from app.models import Currency, Item, PriceObservation, PriceSource
from app.services import prices

TODAY = date(2026, 9, 1)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ensure_kitchen(s)
        yield s


def _item(session: Session, slug: str) -> Item:
    return session.exec(select(Item).where(Item.slug == slug)).first()


def _obs(
    session: Session,
    slug: str,
    per_kg: float,
    days_ago: int = 0,
    source: PriceSource = PriceSource.PURCHASE,
) -> None:
    item = _item(session, slug)
    # Quote per kilo, record as paid-for-a-quantity, which is what a till gives you.
    prices.record(
        session,
        item,
        paid=per_kg,
        quantity=1000,
        source=source,
        observed_on=TODAY - timedelta(days=days_ago),
    )
    session.commit()


def _est(session: Session, slug: str):
    return prices.estimate(session, _item(session, slug), on=TODAY)


# --- nothing invented --------------------------------------------------------


def test_no_observations_means_no_estimate(session):
    """A made-up price here costs somebody money, so there isn't one."""
    assert _est(session, "rice") is None


def test_a_zero_price_is_not_recorded(session):
    assert prices.record(session, _item(session, "rice"), 0, 1000, PriceSource.MANUAL) is None
    assert prices.record(session, _item(session, "rice"), 5, 0, PriceSource.MANUAL) is None


def test_one_observation_gives_that_price(session):
    _obs(session, "rice", 5.60)
    est = _est(session, "rice")
    assert est.price == 5.60
    assert est.observations == 1
    assert est.quoted_per == 1000


def test_items_sold_by_the_piece_are_quoted_per_piece(session):
    item = _item(session, "eggs")
    prices.record(session, item, paid=12.0, quantity=12, source=PriceSource.PURCHASE,
                  observed_on=TODAY)
    session.commit()
    est = prices.estimate(session, item, on=TODAY)
    assert est.quoted_per == 1
    assert est.price == 1.0


# --- recency ------------------------------------------------------------------


def test_recent_prices_dominate_old_ones(session):
    _obs(session, "rice", 4.00, days_ago=120)
    _obs(session, "rice", 12.00, days_ago=1)

    est = _est(session, "rice")
    # A flat mean would say 8.00. The recent price is what someone would pay.
    assert est.price > 11.0


def test_an_old_price_still_counts_for_something(session):
    _obs(session, "rice", 4.00, days_ago=30)
    _obs(session, "rice", 12.00, days_ago=0)
    est = _est(session, "rice")
    assert est.price < 12.00


def test_the_half_life_is_what_it_claims(session):
    """One half-life old should carry half the weight."""
    _obs(session, "rice", 10.00, days_ago=0)
    _obs(session, "rice", 20.00, days_ago=int(settings.price_half_life_days))
    # weights 1.0 and 0.5 -> (10 + 10) / 1.5
    assert _est(session, "rice").price == pytest.approx(13.33, abs=0.05)


def test_stale_when_nothing_is_recent(session):
    _obs(session, "rice", 5.60, days_ago=90)
    est = _est(session, "rice")
    assert est.stale
    assert est.certainty == "poor"
    assert est.newest_days_old == 90


def test_not_stale_when_something_is_recent(session):
    _obs(session, "rice", 5.60, days_ago=90)
    _obs(session, "rice", 6.00, days_ago=2)
    assert not _est(session, "rice").stale


# --- disagreement is reported, not hidden -------------------------------------


def test_the_spread_is_reported(session):
    for price in (5.0, 9.0, 13.0):
        _obs(session, "rice", price, days_ago=3)

    est = _est(session, "rice")
    assert est.low == 5.0
    assert est.high == 13.0
    assert est.spread_pct > 50


def test_a_wide_spread_lowers_certainty(session):
    for price in (5.0, 9.0, 20.0):
        _obs(session, "rice", price, days_ago=2)
    wide = _est(session, "rice")

    for price in (9.0, 9.2, 9.4):
        _obs(session, "lentils", price, days_ago=2)
    tight = _est(session, "lentils")

    assert tight.certainty == "good"
    assert wide.certainty != "good"


def test_old_observations_do_not_widen_todays_range(session):
    _obs(session, "rice", 2.00, days_ago=400)
    _obs(session, "rice", 9.00, days_ago=1)
    _obs(session, "rice", 9.50, days_ago=2)

    est = _est(session, "rice")
    assert est.low >= 9.0, "a price from last year is not part of this month's range"


# --- source trust -------------------------------------------------------------


def test_first_hand_beats_a_reference_average(session):
    _obs(session, "rice", 10.00, days_ago=1, source=PriceSource.PURCHASE)
    _obs(session, "rice", 4.00, days_ago=1, source=PriceSource.REFERENCE)

    # Weights 1.0 vs 0.6 — what the household paid pulls harder.
    assert _est(session, "rice").price > 7.0


def test_sources_are_reported(session):
    _obs(session, "rice", 10.00, source=PriceSource.PURCHASE)
    _obs(session, "rice", 9.00, source=PriceSource.REFERENCE)
    assert set(_est(session, "rice").sources) == {"purchase", "reference"}


def test_a_manual_correction_is_an_observation_not_an_override(session):
    """It stays current instead of accumulating stale fixes."""
    _obs(session, "rice", 20.00, days_ago=0, source=PriceSource.MANUAL)
    corrected = _est(session, "rice").price
    assert corrected == 20.00

    # Weeks later, a fresh purchase should move it without anyone clearing the
    # earlier correction.
    later = prices.estimate(
        session, _item(session, "rice"), on=TODAY + timedelta(days=60)
    )
    assert later.stale, "the old correction does not stay authoritative for ever"


def test_currencies_do_not_mix(session):
    item = _item(session, "rice")
    prices.record(session, item, 5.60, 1000, PriceSource.PURCHASE,
                  currency=Currency.ILS, observed_on=TODAY)
    prices.record(session, item, 1.50, 1000, PriceSource.PURCHASE,
                  currency=Currency.USD, observed_on=TODAY)
    session.commit()

    assert prices.estimate(session, item, on=TODAY, currency=Currency.ILS).price == 5.60
    assert prices.estimate(session, item, on=TODAY, currency=Currency.USD).price == 1.50


# --- bulk --------------------------------------------------------------------


def test_bulk_estimates_skip_items_with_no_data(session):
    _obs(session, "rice", 5.60)
    items = {i.id: i for i in session.exec(select(Item))}
    found = prices.estimates(session, items, on=TODAY)

    assert _item(session, "rice").id in found
    assert _item(session, "lentils").id not in found


def test_bulk_and_single_agree(session):
    _obs(session, "rice", 5.60, days_ago=3)
    _obs(session, "rice", 7.00, days_ago=1)

    items = {i.id: i for i in session.exec(select(Item))}
    bulk = prices.estimates(session, items, on=TODAY)[_item(session, "rice").id]
    assert bulk.price == _est(session, "rice").price
