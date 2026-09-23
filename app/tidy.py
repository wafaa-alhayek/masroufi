"""Turn a bank note into a vendor name worth keeping.

A statement note is not a vendor name. It is a vendor name wrapped in whatever
the payment network added: `POS PURCHASE VISA AL AMAL SUPERMARKET 3352119 GAZA`.
Storing that as-is gives a dataset nobody can group, chart, or compare prices
across, so the wrapping comes off before anything is stored.

These are pure string functions with no model involved — they are exact, free,
and run before the translation and classification steps.
"""

import re

# Words the payment network adds, which say nothing about who was paid. A note
# made only of these is treated as having no vendor name at all.
FILLER_TOKENS: frozenset[str] = frozenset(
    {
        "pos",
        "atm",
        "ref",
        "reference",
        "misc",
        "trx",
        "trn",
        "txn",
        "transaction",
        "debit",
        "credit",
        "card",
        "cards",
        "visa",
        "mastercard",
        "maestro",
        "purchase",
        "payment",
        "pmt",
        "withdrawal",
        "unknown",
        "branch",
        "terminal",
        "merchant",
        "dr",
        "cr",
        "no",
        "nr",
    }
)

# Arabic equivalents of the same filler.
FILLER_TOKENS_AR: frozenset[str] = frozenset(
    {"عملية", "حركة", "بطاقة", "مرجع", "شراء", "دفعة", "سحب", "فرع", "رقم"}
)

_ALL_FILLER = FILLER_TOKENS | FILLER_TOKENS_AR

# Words that should stay upper-case in a display name rather than being
# title-cased into something odd.
_KEEP_UPPER = frozenset({"utc", "id", "uae", "usd", "ils", "jod", "nis"})

_PUNCT = re.compile(r"[^\w؀-ۿ]+")
_ARABIC = re.compile(r"[؀-ۿ]")

# Markers left behind by redaction. Matched in their bracketed form only, so a
# shop actually called "Phone Shop" keeps its name.
_PLACEHOLDER = re.compile(r"\[(?:NUM|IBAN|CARD|PHONE|EMAIL)\]")


def tokens(text: str) -> list[str]:
    return [t for t in _PUNCT.sub(" ", _PLACEHOLDER.sub(" ", text)).split() if t]


def strip_filler(text: str) -> str:
    """Drop payment-network words and bare numbers, keep the rest in order."""
    kept = [
        t
        for t in tokens(text)
        if t.casefold() not in _ALL_FILLER and not t.isdigit() and len(t) > 1
    ]
    return " ".join(kept)


def is_filler_only(text: str) -> bool:
    """True when nothing identifying survives — `POS 3352119` and friends."""
    return not strip_filler(text)


def display_name(text: str) -> str:
    """A stable, readable vendor name for storing and showing.

    Arabic is left alone: it has no letter case, and title-casing a mixed string
    would mangle the Latin half anyway.
    """
    stripped = strip_filler(text)
    if not stripped:
        return ""
    if _ARABIC.search(stripped):
        return re.sub(r"\s+", " ", stripped).strip()

    # Deliberately no "short and upper-case means an initialism" rule: in
    # transliterated Arabic the short upper-case tokens are articles and
    # particles — AL, EL, ABU — and "AL Amal" reads worse than "Al Amal" more
    # often than a genuine initialism gets flattened.
    return " ".join(
        token.upper() if token.casefold() in _KEEP_UPPER else token.capitalize()
        for token in stripped.split()
    )


def match_key(text: str) -> str:
    """Case-folded, filler-free, order-insensitive key for matching vendors.

    Sorting the tokens means `AL AMAL SUPERMARKET` and `SUPERMARKET AL AMAL`
    resolve to the same vendor.
    """
    stripped = strip_filler(text).casefold()
    return " ".join(sorted(stripped.split()))
