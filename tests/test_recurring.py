from datetime import date, timedelta

from app.models import Currency, Transaction
from app.redact import normalise
from app.services.recurring import find_patterns


def _tx(tid: int, day: int, amount: float, note: str) -> Transaction:
    return Transaction(
        id=tid,
        booked_on=date(2026, 1, 1) + timedelta(days=day),
        amount=amount,
        currency=Currency.ILS,
        note=note,
        note_key=normalise(note),
        import_batch="test",
    )


def test_detects_repeat_vendor():
    txs = [_tx(i, i * 7, -60.0, "مياه - تانكر") for i in range(4)]
    patterns = find_patterns(txs)
    assert len(patterns) == 1
    assert patterns[0].occurrences == 4
    assert patterns[0].median_gap_days == 7
    assert not patterns[0].by_amount_only


def test_detects_the_three_shekel_case_by_amount():
    # Different useless reference each time, same fare.
    txs = [_tx(i, i * 2, -3.0, f"POS {900000 + i}") for i in range(6)]
    patterns = find_patterns(txs)
    assert len(patterns) == 1
    assert patterns[0].by_amount_only
    assert patterns[0].occurrences == 6


def test_named_vendor_not_reported_twice():
    txs = [_tx(i, i * 7, -60.0, "مياه - تانكر") for i in range(4)]
    patterns = find_patterns(txs)
    assert all(not p.by_amount_only for p in patterns)


def test_below_threshold_is_not_a_pattern():
    txs = [_tx(i, i * 7, -60.0, "مياه") for i in range(2)]
    assert find_patterns(txs) == []


def test_monthly_estimate_scales_by_cadence():
    weekly = [_tx(i, i * 7, -60.0, "مياه") for i in range(5)]
    pattern = find_patterns(weekly)[0]
    # ~4.3 occurrences a month at 60 each.
    assert 250 < pattern.monthly_estimate < 270


def test_ordered_by_monthly_cost():
    txs = [_tx(i, i * 7, -60.0, "مياه") for i in range(5)]
    txs += [_tx(100 + i, i * 7, -200.0, "ايجار") for i in range(5)]
    patterns = find_patterns(txs)
    assert patterns[0].note_key == normalise("ايجار")
