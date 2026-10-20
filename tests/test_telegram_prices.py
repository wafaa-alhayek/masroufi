"""Prices from the public channel t.me/asarsook: parsing, and loading."""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.importers.telegram_prices import (
    WIQIYA_G,
    catalogue_items,
    parse_export,
    parse_line,
    read_csv,
)
from app.models import Item, PriceObservation, PriceSource
from app.services import prices
from app.services.prices import CHANNEL_PRICES, load_channel_prices


@pytest.fixture(scope="module")
def items():
    return catalogue_items()


def line(text, items):
    return parse_line(text, items, message_id=1, posted_on="2025-06-01", region="gaza")


def test_a_kilo_price(items):
    row = line("كيلو بطاطا 60 شيكل", items)
    assert (row.status, row.item_slug, row.quantity, row.per_unit) == ("ok", "potato", 1000, 0.06)


def test_a_wiqiya_is_a_quarter_kilo(items):
    row = line("• 🧄 وقية الثوم 15ش.", items)
    assert row.item_slug == "garlic"
    assert row.quantity == WIQIYA_G
    assert row.per_unit == pytest.approx(15 / 250)


def test_a_range_is_kept_and_costed_at_its_middle(items):
    row = line("- 🌿 ملوخية: 18–20₪", items)
    assert (row.price_low, row.price_high) == (18, 20)
    assert row.item_slug == "molokhia_fresh", "fresh molokhia is not the dried kind"
    assert row.unit_inferred and row.per_unit == pytest.approx(0.019)


def test_arabic_digits_and_half_a_kilo(items):
    row = line("• شعيرية نص كيلو : ١٣₪", items)
    assert (row.item_slug, row.quantity, row.per_unit) == ("vermicelli", 500, 0.026)


def test_a_litre_of_oil_and_five_written_as_a_word(items):
    assert line("لتر سيرج 80 شيكل", items).quantity == 1000
    assert line("سيرج ١٥٠ ش خمسة لتر", items).quantity == 5000


def test_green_chilli_is_not_sweet_pepper(items):
    """In Gaza فلفل أخضر is the hot chilli, at several times the price."""
    assert line("وقية فلفل أخضر 100 شيكل", items).item_slug == "hot_green_chilli"
    assert line("كيلو فليفلة 100 شيكل", items).item_slug == "pepper"


def test_cash_first_then_app(items):
    row = line("🔅 لتر السيرج بـ 27₪ (33₪ تطبيق)", items)
    assert (row.price_low, row.payment) == (27, "cash")
    assert line("• كيلو سميد: 24 شيكل (📲 تطبيق)", items).payment == "app"


@pytest.mark.parametrize(
    "text, status",
    [
        ("🍋 حمض الليمون – 600 شيكل / كيلو", "not_the_item"),  # citric acid
        ("• دير البلح:  10 شيكل", "unmatched_item"),  # a town, not dates
        ("💲الدولار = 3.65 شيكل", "not_a_price"),
        ("• 🫘 علبة فول: 20 شيكل", "tin_without_weight"),
        ("• اندومي: 5 ₪", "no_quantity"),  # a packet, not a kilo
        ("اللي كان يشتري سكر ب500شيكل", "prose"),
    ],
)
def test_what_is_left_out(items, text, status):
    assert line(text, items).status == status


def test_a_line_with_no_shekel_price_is_not_a_row(items):
    assert line("🗓 14 ابريل  2025 في مدينة غزة:", items) is None


EXPORT = """
<div class="message default clearfix" id="message12">
 <div class="body">
  <div class="pull_right date details" title="23.11.2024 10:06:53 UTC+02:00">10:06</div>
  <div class="text">📊 <strong>الأسعار في شمال غزة اليوم<br>🗓️ 23 نوفمبر 2024<br></strong><br>كيلو بطاطا 60 شيكل <br><br>راس ثومة 15 شيكل</div>
 </div>
</div>
<div class="message default clearfix" id="message13">
 <div class="body">
  <div class="pull_right date details" title="24.11.2024 09:00:00 UTC+02:00">09:00</div>
  <div class="text">الأسعار في جنوب قطاع غزة<br>كيلو سكر 35 شيكل</div>
 </div>
</div>
"""


def test_an_export_becomes_dated_regional_rows(items):
    rows = parse_export(EXPORT, items)
    ok = [r for r in rows if r.usable]
    assert [(r.message_id, r.posted_on, r.region, r.item_slug) for r in ok] == [
        (12, "2024-11-23", "north_gaza", "potato"),
        (13, "2024-11-24", "south_gaza", "sugar"),
    ]
    # A head of garlic has no weight, so it is kept as a row but not as a price.
    assert any(r.item_slug == "garlic" and r.status == "unit_mismatch" for r in rows)


def test_every_committed_price_names_a_seeded_item():
    from app.kitchen.catalogue import seed_items

    slugs = {i.slug for i in seed_items()}
    rows = [r for r in read_csv(CHANNEL_PRICES) if r.usable]
    assert len(rows) > 1000
    assert {r.item_slug for r in rows} <= slugs
    assert all(r.quantity > 0 and r.per_unit > 0 for r in rows)


@pytest.fixture
def session():
    from app import idempotency  # noqa: F401
    from app.kitchen import ensure_kitchen

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        ensure_kitchen(s)
        yield s


def test_loading_is_idempotent_and_skips_app_prices(session):
    first = load_channel_prices(session)
    assert first > 1000
    assert load_channel_prices(session) == 0

    rows = [r for r in read_csv(CHANNEL_PRICES) if r.usable]
    # A line repeated word for word in the same post is one price, not two.
    distinct = {
        (r.item_slug, r.posted_on, r.message_id, r.line) for r in rows if r.payment != "app"
    }
    assert first == len(distinct)
    observations = session.exec(select(PriceObservation)).all()
    assert {o.source for o in observations} == {PriceSource.CHANNEL}
    assert all(o.note.startswith("t.me/asarsook/") for o in observations)


def test_a_new_install_has_a_dated_estimate_not_no_price(session):
    load_channel_prices(session)
    tomato = session.exec(select(Item).where(Item.slug == "tomato")).one()
    est = prices.estimate(session, tomato)
    assert est is not None and est.price > 0
    # The newest post is from December 2025: the estimate must say it is old.
    assert est.stale and est.certainty == "poor"
    assert est.sources == ["channel"]


def test_a_phone_number_is_never_kept(items):
    row = line("للطلب 0597 702 724 كيلو بطاطا 60 شيكل", items)
    assert "0597" not in row.line and "[NUM]" in row.line
