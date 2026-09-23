"""Movements between the household's own accounts.

The scenario throughout: a JawwalPay wallet topped up from a Bank of Palestine
account. The bank shows -200, the wallet shows +200, and only one movement
happened.
"""

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Currency, Source, SourceKind, Transaction
from app.redact import normalise
from app.services import transfers


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all(
            [
                Source(slug="bop", name="Bank of Palestine", kind=SourceKind.BANK),
                Source(slug="jawwalpay", name="JawwalPay", kind=SourceKind.WALLET),
            ]
        )
        s.commit()
        yield s


def _source_id(session: Session, slug: str) -> int:
    return session.exec(select(Source).where(Source.slug == slug)).first().id


def _add(session: Session, slug: str, amount: float, note: str, day: int) -> Transaction:
    t = Transaction(
        booked_on=date(2026, 1, day),
        amount=amount,
        currency=Currency.ILS,
        note=note,
        note_key=normalise(note),
        source_id=_source_id(session, slug),
        import_batch="test",
    )
    session.add(t)
    session.flush()
    return t


def test_named_top_up_is_linked_confidently(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    _add(session, "jawwalpay", 200.0, "top up", 5)

    candidates = transfers.find_candidates(session)
    assert len(candidates) == 1
    assert candidates[0].confident
    assert "jawwalpay" in candidates[0].reason


def test_linking_excludes_both_sides_from_spending(session):
    debit = _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    credit = _add(session, "jawwalpay", 200.0, "top up", 5)

    assert transfers.link_confident(session) == 1
    session.commit()

    for t, peer in ((debit, credit), (credit, debit)):
        session.refresh(t)
        assert t.is_internal_transfer
        assert t.transfer_peer_id == peer.id


def test_unnamed_pair_is_offered_but_not_linked(session):
    _add(session, "bop", -200.0, "POS 4412", 5)
    _add(session, "jawwalpay", 200.0, "MISC 9981", 5)

    candidates = transfers.find_candidates(session)
    assert len(candidates) == 1
    assert not candidates[0].confident, "two unrelated 200s would look identical"
    assert transfers.link_confident(session) == 0


def test_same_source_is_never_a_transfer(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    _add(session, "bop", 200.0, "refund", 5)
    assert transfers.find_candidates(session) == []


def test_outside_the_window_is_not_a_pair(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 1)
    _add(session, "jawwalpay", 200.0, "top up", 20)
    assert transfers.find_candidates(session) == []


def test_unequal_amounts_are_not_a_pair(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    _add(session, "jawwalpay", 195.0, "top up", 5)
    assert transfers.find_candidates(session) == []


def test_different_currencies_are_not_a_pair(session):
    a = _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    b = _add(session, "jawwalpay", 200.0, "top up", 5)
    b.currency = Currency.USD
    session.add(b)
    session.flush()
    assert transfers.find_candidates(session) == []


def test_nearest_date_wins_when_amounts_repeat(session):
    debit = _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    far = _add(session, "jawwalpay", 200.0, "top up", 7)
    near = _add(session, "jawwalpay", 200.0, "top up", 5)

    candidates = transfers.find_candidates(session)
    assert len(candidates) == 1
    assert candidates[0].out_id == debit.id
    assert candidates[0].in_id == near.id, "the same-day arrival is the better match"
    assert far.id not in {c.in_id for c in candidates}


def test_a_credit_is_only_used_once(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 6)
    _add(session, "jawwalpay", 200.0, "top up", 5)

    candidates = transfers.find_candidates(session)
    assert len(candidates) == 1, "one arrival cannot settle two departures"


def test_already_linked_pairs_are_not_offered_again(session):
    _add(session, "bop", -200.0, "تحويل الى JawwalPay", 5)
    _add(session, "jawwalpay", 200.0, "top up", 5)
    transfers.link_confident(session)
    session.commit()
    assert transfers.find_candidates(session) == []
