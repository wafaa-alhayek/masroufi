"""Find repeat transactions by arithmetic, not by the model.

Two kinds of repeat, because the user described both:

  * Same note, recurring — a named vendor the household uses regularly.
  * Same amount, recurring, note missing or useless — the ₪3 case. Three shekels
    charged over and over is almost certainly a shared-taxi fare, but the app
    should not assert that; it proposes the pattern and the user names it.

The model's job is only to suggest a label for a group once it exists. Detection
itself is exact and free.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median

from app import tidy
from app.models import Transaction

MIN_OCCURRENCES = 3

def _has_useful_note(note_key: str) -> bool:
    """A note made only of payment-network filler ("POS 3352119") is no note at
    all, so it falls through to the amount-based pass rather than grouping every
    card purchase in the statement under one meaningless key."""
    return bool(tidy.strip_filler(note_key))


@dataclass
class Pattern:
    note_key: str
    amount: float
    currency: str
    occurrences: int
    median_gap_days: float
    first_seen: date
    last_seen: date
    by_amount_only: bool
    transaction_ids: list[int]

    @property
    def monthly_estimate(self) -> float:
        """Roughly what this pattern costs per month, if it keeps its cadence."""
        if self.median_gap_days <= 0:
            return abs(self.amount) * self.occurrences
        return abs(self.amount) * (30.44 / self.median_gap_days)


def find_patterns(
    transactions: list[Transaction], min_occurrences: int = MIN_OCCURRENCES
) -> list[Pattern]:
    """Group transactions into recurring patterns, most costly first."""
    patterns = _group(transactions, min_occurrences, by_amount_only=False)

    # Only look for amount-only patterns among transactions the note-based pass
    # could not explain, so a named vendor is not reported twice.
    explained = {tid for p in patterns for tid in p.transaction_ids}
    leftovers = [t for t in transactions if t.id not in explained]
    patterns += _group(leftovers, min_occurrences, by_amount_only=True)

    return sorted(patterns, key=lambda p: p.monthly_estimate, reverse=True)


def _group(
    transactions: list[Transaction], min_occurrences: int, by_amount_only: bool
) -> list[Pattern]:
    buckets: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.id is None:
            continue
        if by_amount_only:
            key = (round(t.amount, 2), t.currency.value)
        else:
            if not _has_useful_note(t.note_key):
                continue
            key = (t.note_key, round(t.amount, 2), t.currency.value)
        buckets[key].append(t)

    out: list[Pattern] = []
    for key, group in buckets.items():
        if len(group) < min_occurrences:
            continue
        group.sort(key=lambda t: t.booked_on)
        dates = [t.booked_on for t in group]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        out.append(
            Pattern(
                note_key="" if by_amount_only else key[0],
                amount=group[0].amount,
                currency=group[0].currency.value,
                occurrences=len(group),
                median_gap_days=float(median(gaps)) if gaps else 0.0,
                first_seen=dates[0],
                last_seen=dates[-1],
                by_amount_only=by_amount_only,
                transaction_ids=[t.id for t in group if t.id is not None],
            )
        )
    return out
