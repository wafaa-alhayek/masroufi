"""Strip identifiers out of a transaction note before it leaves the machine.

The classifier only needs the merchant-ish text to decide a category, so
anything that identifies an account, a card or a person is removed first. This
runs on every note on the way to the classifier, not on the note we store and
show the user.
"""

import re

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# 13-19 digits, optionally grouped by spaces or dashes: card numbers.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Any other run of 6+ digits: account numbers, reference numbers, national IDs.
_LONG_DIGITS = re.compile(r"\b\d{6,}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
# Local mobile numbers, with or without country code.
_PHONE = re.compile(r"\+?\d{2,4}[- ]?\d{3}[- ]?\d{4,}")

_PATTERNS = (
    (_IBAN, "[IBAN]"),
    (_EMAIL, "[EMAIL]"),
    (_CARD, "[CARD]"),
    (_PHONE, "[PHONE]"),
    (_LONG_DIGITS, "[NUM]"),
)


def redact(note: str) -> str:
    """Remove account, card, phone and email identifiers from a note."""
    out = note
    for pattern, placeholder in _PATTERNS:
        out = pattern.sub(placeholder, out)
    return re.sub(r"\s+", " ", out).strip()


_TRAILING_REF = re.compile(r"[\s#*/-]+$")


def normalise(note: str) -> str:
    """A stable key for caching and for grouping repeat vendors.

    Case-folded, punctuation-light, and with the redaction placeholders dropped
    so that the same shop with different reference numbers collapses to one key.
    """
    out = redact(note).casefold()
    out = re.sub(r"\[(?:iban|email|card|phone|num)\]", " ", out)
    out = re.sub(r"[^\w؀-ۿ ]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()
