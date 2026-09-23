"""Keep re-imports from duplicating transactions.

The obvious approach — treat (date, amount, note) as unique — is wrong here.
Two ₪3 shared-taxi fares on the same day with the same useless note are two real
fares, not a duplicate. So this counts instead of deduplicating: if the file
contains three identical rows and the database already holds two, one is new.

A provider reference is used instead whenever the export supplies one, since
that is exact.
"""

from collections import Counter

from sqlmodel import Session, select

from app.models import Transaction


def _fingerprint(t: Transaction) -> tuple:
    return (t.source_id, t.booked_on, round(t.amount, 2), t.note_key)


def filter_new(
    session: Session, incoming: list[Transaction]
) -> tuple[list[Transaction], int]:
    """Split an import into rows to store and a count of rows already held."""
    if not incoming:
        return [], 0

    source_ids = {t.source_id for t in incoming}
    existing = list(
        session.exec(select(Transaction).where(Transaction.source_id.in_(source_ids)))
    )

    known_external = {t.external_id for t in existing if t.external_id}
    held = Counter(_fingerprint(t) for t in existing)

    fresh: list[Transaction] = []
    duplicates = 0
    seen_external: set[str] = set()

    for t in incoming:
        if t.external_id:
            # The provider's own reference is authoritative.
            if t.external_id in known_external or t.external_id in seen_external:
                duplicates += 1
                continue
            seen_external.add(t.external_id)
            fresh.append(t)
            continue

        key = _fingerprint(t)
        if held[key] > 0:
            held[key] -= 1
            duplicates += 1
            continue
        fresh.append(t)

    return fresh, duplicates
