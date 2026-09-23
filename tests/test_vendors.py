import asyncio
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CategorySource, Currency, Transaction, Vendor, VendorAlias
from app.notes import NoteCleaner
from app.redact import normalise
from app.services import vendors as vendor_service
from app.translate.base import NoopTranslator


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _clean(raw: str):
    return asyncio.run(NoteCleaner(NoopTranslator()).clean(raw))


def _tx(note: str, amount: float = -100.0, day: int = 1) -> Transaction:
    return Transaction(
        booked_on=date(2026, 1, day),
        amount=amount,
        currency=Currency.ILS,
        note=note,
        note_key=normalise(note),
        import_batch="test",
    )


def test_creates_a_vendor_with_a_tidy_name(session):
    vendor = vendor_service.resolve(session, _clean("POS AL AMAL SUPERMARKET 3352119"))
    assert vendor is not None
    assert vendor.name == "Al Amal Supermarket"


def test_same_shop_different_reference_is_one_vendor(session):
    a = vendor_service.resolve(session, _clean("POS AL AMAL SUPERMARKET 884213"))
    b = vendor_service.resolve(session, _clean("VISA AL AMAL SUPERMARKET 991204"))
    assert a.id == b.id
    assert len(list(session.exec(select(Vendor)))) == 1


def test_word_order_does_not_create_a_second_vendor(session):
    a = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    b = vendor_service.resolve(session, _clean("SUPERMARKET AL AMAL"))
    assert a.id == b.id


def test_near_miss_spelling_merges_and_records_the_alias(session):
    a = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    b = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKT"))
    assert a.id == b.id
    aliases = list(session.exec(select(VendorAlias).where(VendorAlias.vendor_id == a.id)))
    assert len(aliases) == 2, "both spellings are kept, so a bad merge can be traced"


def test_distinct_shops_stay_distinct(session):
    a = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    b = vendor_service.resolve(session, _clean("AL SALAM PHARMACY"))
    assert a.id != b.id


def test_filler_only_note_has_no_vendor(session):
    assert vendor_service.resolve(session, _clean("POS 3352119")) is None
    assert list(session.exec(select(Vendor))) == []


def test_sightings_accumulate(session):
    vendor = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    for day, amount in ((3, -145.5), (10, -132.0), (17, -151.25)):
        vendor_service.record_sighting(vendor, _tx("AL AMAL", amount, day))

    assert vendor.times_seen == 3
    assert vendor.total_spent == pytest.approx(428.75)
    assert vendor.first_seen == date(2026, 1, 3)
    assert vendor.last_seen == date(2026, 1, 17)


def test_incoming_money_does_not_count_as_spending(session):
    vendor = vendor_service.resolve(session, _clean("SALARY PAYER"))
    vendor_service.record_sighting(vendor, _tx("SALARY PAYER", 1200.0))
    assert vendor.times_seen == 1
    assert vendor.total_spent == 0.0


def test_learning_backfills_unreviewed_transactions(session):
    vendor = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    session.flush()

    pending = [_tx("AL AMAL SUPERMARKET", -100.0, day) for day in (3, 10, 17)]
    for t in pending:
        t.vendor_id = vendor.id
        t.needs_review = True
        session.add(t)
    session.flush()

    settled = vendor_service.learn_category(session, vendor, "groceries")
    session.commit()

    assert settled == 3
    assert vendor.category_confirmed
    for t in pending:
        session.refresh(t)
        assert t.category == "groceries"
        assert t.category_source is CategorySource.RULE
        assert not t.needs_review


def test_learning_does_not_overwrite_a_user_answer(session):
    vendor = vendor_service.resolve(session, _clean("AL AMAL SUPERMARKET"))
    session.flush()

    answered = _tx("AL AMAL SUPERMARKET", -100.0, 3)
    answered.vendor_id = vendor.id
    answered.category = "health"
    answered.category_source = CategorySource.USER
    answered.needs_review = False
    session.add(answered)
    session.flush()

    settled = vendor_service.learn_category(session, vendor, "groceries")
    session.commit()

    assert settled == 0
    session.refresh(answered)
    assert answered.category == "health", "the household's own answer stands"
