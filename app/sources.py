"""The payment providers a household actually uses.

Seeded rather than hardcoded in the schema, so adding a provider is a row and
not a migration. Slugs are stable and are what the import endpoint takes.
"""

from sqlmodel import Session, select

from app.models import Currency, Source, SourceKind

DEFAULT_SOURCES: tuple[dict, ...] = (
    {
        "slug": "bop",
        "name": "Bank of Palestine",
        "kind": SourceKind.BANK,
        "default_currency": Currency.ILS,
    },
    {
        "slug": "jawwalpay",
        "name": "JawwalPay",
        "kind": SourceKind.WALLET,
        "default_currency": Currency.ILS,
    },
    {
        "slug": "palpay",
        "name": "PalPay",
        "kind": SourceKind.WALLET,
        "default_currency": Currency.ILS,
    },
    {
        "slug": "cash",
        "name": "Cash",
        "kind": SourceKind.CASH,
        "default_currency": Currency.ILS,
    },
    {
        "slug": "manual",
        "name": "Entered by hand",
        "kind": SourceKind.MANUAL,
        "default_currency": Currency.ILS,
    },
)

# Words that suggest a transaction is topping up or drawing on another provider
# rather than paying a shop. Used to tell a confident internal transfer from a
# coincidence of equal amounts.
TRANSFER_HINTS: dict[str, tuple[str, ...]] = {
    "jawwalpay": ("jawwalpay", "jawwal pay", "جوال باي", "جوالباي"),
    "palpay": ("palpay", "pal pay", "بال باي", "بالباي"),
    "bop": ("bank of palestine", "bop", "بنك فلسطين"),
    "cash": ("atm", "withdrawal", "سحب", "نقدي"),
}


def ensure_sources(session: Session) -> None:
    """Create any missing default source. Safe to call on every startup."""
    existing = {s.slug for s in session.exec(select(Source))}
    added = [Source(**row) for row in DEFAULT_SOURCES if row["slug"] not in existing]
    if added:
        session.add_all(added)
        session.commit()


def by_slug(session: Session, slug: str) -> Source | None:
    return session.exec(select(Source).where(Source.slug == slug)).first()
