"""Recognise money the household moved between its own accounts.

A JawwalPay wallet topped up from a Bank of Palestine account produces two rows:
-200 in the bank export and +200 in the wallet export. Both are real records, but
only one movement happened and none of it was spent. Counting both inflates the
household's spending by exactly what they moved between their own pockets.

Matching is equal-and-opposite amounts, across different sources, within a few
days. That alone is not enough — two unrelated ₪200 transactions a day apart
would match — so a pair is auto-linked only when one side's note names the other
provider. Everything else is offered to the household to confirm, which follows
the same rule as duplicate receipts: match confidently, ask when unsure.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session, select

from app.models import Source, Transaction
from app.sources import TRANSFER_HINTS

WINDOW_DAYS = 3


@dataclass
class TransferCandidate:
    out_id: int
    in_id: int
    amount: float
    currency: str
    out_source: str
    in_source: str
    days_apart: int
    confident: bool
    reason: str


def find_candidates(session: Session, window_days: int = WINDOW_DAYS) -> list[TransferCandidate]:
    """Pair up unlinked equal-and-opposite transactions across sources."""
    rows = list(
        session.exec(select(Transaction).where(Transaction.is_internal_transfer == False))  # noqa: E712
    )
    names = {s.id: s.slug for s in session.exec(select(Source))}

    outgoing = [t for t in rows if t.amount < 0 and t.source_id is not None]
    incoming = [t for t in rows if t.amount > 0 and t.source_id is not None]

    used: set[int] = set()
    out: list[TransferCandidate] = []

    # Nearest date first, so an obvious same-day pair is not consumed by a
    # three-day-old one of the same amount.
    for debit in sorted(outgoing, key=lambda t: t.booked_on):
        best: tuple[int, Transaction] | None = None
        for credit in incoming:
            if credit.id in used or credit.source_id == debit.source_id:
                continue
            if credit.currency != debit.currency:
                continue
            if round(credit.amount, 2) != round(-debit.amount, 2):
                continue
            gap = abs((credit.booked_on - debit.booked_on).days)
            if gap > window_days:
                continue
            if best is None or gap < best[0]:
                best = (gap, credit)

        if best is None:
            continue

        gap, credit = best
        used.add(credit.id)

        out_slug = names.get(debit.source_id, "?")
        in_slug = names.get(credit.source_id, "?")
        confident, reason = _is_confident(debit, credit, out_slug, in_slug)

        out.append(
            TransferCandidate(
                out_id=debit.id,
                in_id=credit.id,
                amount=abs(debit.amount),
                currency=debit.currency.value,
                out_source=out_slug,
                in_source=in_slug,
                days_apart=gap,
                confident=confident,
                reason=reason,
            )
        )

    return out


def _is_confident(
    debit: Transaction, credit: Transaction, out_slug: str, in_slug: str
) -> tuple[bool, str]:
    """A pair is only auto-linked when a note names the provider on the other side."""
    debit_note = debit.note_key
    credit_note = credit.note_key

    if any(hint in debit_note for hint in TRANSFER_HINTS.get(in_slug, ())):
        return True, f"the {out_slug} note names {in_slug}"
    if any(hint in credit_note for hint in TRANSFER_HINTS.get(out_slug, ())):
        return True, f"the {in_slug} note names {out_slug}"

    return False, (
        f"same amount {abs(debit.amount):.2f} {debit.currency.value} "
        f"{'the same day' if debit.booked_on == credit.booked_on else 'a few days apart'}, "
        "but neither note names the other account"
    )


def link(session: Session, debit: Transaction, credit: Transaction) -> None:
    """Mark a pair as one internal movement, excluded from spending."""
    for transaction, peer in ((debit, credit), (credit, debit)):
        transaction.is_internal_transfer = True
        transaction.transfer_peer_id = peer.id
        transaction.needs_review = False
        session.add(transaction)


def link_confident(session: Session, window_days: int = WINDOW_DAYS) -> int:
    """Link every pair we are sure about. Returns how many pairs were linked."""
    linked = 0
    for candidate in find_candidates(session, window_days):
        if not candidate.confident:
            continue
        debit = session.get(Transaction, candidate.out_id)
        credit = session.get(Transaction, candidate.in_id)
        if debit is None or credit is None:
            continue
        link(session, debit, credit)
        linked += 1
    return linked
