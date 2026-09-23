"""Resolve a cleaned note to a vendor, and learn from corrections.

Matching runs in order of confidence:

  1. Exact alias — this spelling has been seen before.
  2. Close alias — the same shop with a typo or a word dropped. Merged only
     above a deliberately high similarity, because wrongly merging two shops
     silently corrupts the household's history and their price data.
  3. New vendor.

Matching is exact-then-conservative on purpose. A missed match leaves two rows
the user can merge later; a wrong match is much harder to notice and undo.
"""

from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.config import settings
from app.models import Transaction, Vendor, VendorAlias
from app.notes import CleanNote


def resolve(session: Session, note: CleanNote) -> Vendor | None:
    """Find or create the vendor for a cleaned note.

    Returns None when the note has no vendor name to work with at all — the
    `POS 3352119` case, which is left to the recurring-amount detector.
    """
    if note.is_filler_only:
        return None

    alias = session.exec(
        select(VendorAlias).where(VendorAlias.alias_key == note.match_key)
    ).first()
    if alias is not None:
        return session.get(Vendor, alias.vendor_id)

    near = _closest(session, note.match_key)
    if near is not None:
        session.add(
            VendorAlias(vendor_id=near.id, alias_key=note.match_key, raw_example=note.raw)
        )
        return near

    vendor = Vendor(name=note.vendor_name, match_key=note.match_key)
    session.add(vendor)
    session.flush()  # assign the id before the alias references it
    session.add(
        VendorAlias(vendor_id=vendor.id, alias_key=note.match_key, raw_example=note.raw)
    )
    return vendor


def _closest(session: Session, match_key: str) -> Vendor | None:
    best: tuple[float, Vendor] | None = None
    for vendor in session.exec(select(Vendor)):
        ratio = SequenceMatcher(None, match_key, vendor.match_key).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, vendor)
    if best is None or best[0] < settings.vendor_match_threshold:
        return None
    return best[1]


def record_sighting(vendor: Vendor, transaction: Transaction) -> None:
    vendor.times_seen += 1
    if transaction.amount < 0:
        vendor.total_spent += abs(transaction.amount)
    if vendor.first_seen is None or transaction.booked_on < vendor.first_seen:
        vendor.first_seen = transaction.booked_on
    if vendor.last_seen is None or transaction.booked_on > vendor.last_seen:
        vendor.last_seen = transaction.booked_on


def learn_category(session: Session, vendor: Vendor, category: str) -> int:
    """Teach the vendor a category from a user correction, and backfill.

    Applied to the vendor's transactions that are still awaiting review — never
    to ones the user has already answered themselves. Returns how many other
    transactions this settled.
    """
    vendor.category = category
    vendor.category_confirmed = True
    session.add(vendor)

    pending = session.exec(
        select(Transaction).where(
            Transaction.vendor_id == vendor.id,
            Transaction.needs_review == True,  # noqa: E712 - SQL comparison, not Python
        )
    ).all()

    from app.models import CategorySource

    for transaction in pending:
        transaction.category = category
        transaction.category_source = CategorySource.RULE
        transaction.confidence = 1.0
        transaction.needs_review = False
        session.add(transaction)

    return len(pending)
