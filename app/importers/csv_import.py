"""Parse a bank statement export into Transaction rows.

Written against no particular bank. Column names vary between exports and
between languages, so headers are matched against a list of aliases and the
debit/credit shape is detected rather than assumed. When a real BOP export is
available, add its headers to the alias lists — that should be the whole change.
"""

import csv
import io
import re
from datetime import date, datetime

from app.models import Currency, Transaction
from app.redact import normalise

_DATE_ALIASES = ("date", "booking date", "value date", "transaction date", "التاريخ", "تاريخ")
_NOTE_ALIASES = (
    "note",
    "notes",
    "description",
    "details",
    "narrative",
    "remarks",
    "beneficiary",
    "البيان",
    "التفاصيل",
    "ملاحظات",
)
_AMOUNT_ALIASES = ("amount", "value", "المبلغ")
_DEBIT_ALIASES = ("debit", "withdrawal", "paid out", "مدين", "سحب")
_CREDIT_ALIASES = ("credit", "deposit", "paid in", "دائن", "إيداع")
_CURRENCY_ALIASES = ("currency", "ccy", "العملة")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d")


class ImportError_(ValueError):
    """The file could not be understood well enough to import."""


def parse_csv(raw: bytes, batch: str, default_currency: Currency = Currency.ILS) -> list[Transaction]:
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_("The file has no header row.")

    headers = {(h or "").strip().casefold(): (h or "") for h in reader.fieldnames}
    date_col = _find(headers, _DATE_ALIASES)
    note_col = _find(headers, _NOTE_ALIASES)
    amount_col = _find(headers, _AMOUNT_ALIASES)
    debit_col = _find(headers, _DEBIT_ALIASES)
    credit_col = _find(headers, _CREDIT_ALIASES)
    currency_col = _find(headers, _CURRENCY_ALIASES)

    if date_col is None:
        raise ImportError_(f"No date column found. Saw: {', '.join(reader.fieldnames)}")
    if note_col is None:
        raise ImportError_(
            "No description or note column found. The note is what makes a "
            "transaction identifiable, so the import cannot proceed without it."
        )
    if amount_col is None and debit_col is None and credit_col is None:
        raise ImportError_("No amount, debit or credit column found.")

    out: list[Transaction] = []
    for lineno, row in enumerate(reader, start=2):
        booked_on = _parse_date(row.get(date_col, ""))
        if booked_on is None:
            continue  # Skip subtotal and footer rows rather than failing the file.

        amount = _parse_amount(row, amount_col, debit_col, credit_col)
        if amount is None:
            continue

        note = (row.get(note_col) or "").strip()
        out.append(
            Transaction(
                booked_on=booked_on,
                amount=amount,
                currency=_parse_currency(row.get(currency_col) if currency_col else None)
                or default_currency,
                note=note,
                note_key=normalise(note),
                import_batch=batch,
            )
        )

    if not out:
        raise ImportError_("No transactions could be read from the file.")
    return out


def _decode(raw: bytes) -> str:
    # utf-8-sig first: Windows bank exports very often carry a BOM. cp1256 is the
    # usual legacy Arabic encoding if utf-8 fails.
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("The file's text encoding could not be determined.")


def _find(headers: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in headers:
            return headers[alias]
    # Fall back to a substring match, for headers like "Transaction Details (EN)".
    for key, original in headers.items():
        if any(alias in key for alias in aliases):
            return original
    return None


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


_NUM_JUNK = re.compile(r"[^\d,.\-()]")


def _parse_amount(
    row: dict, amount_col: str | None, debit_col: str | None, credit_col: str | None
) -> float | None:
    if amount_col:
        return _to_float(row.get(amount_col))

    debit = _to_float(row.get(debit_col)) if debit_col else None
    credit = _to_float(row.get(credit_col)) if credit_col else None
    if debit:
        return -abs(debit)
    if credit:
        return abs(credit)
    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    text = _NUM_JUNK.sub("", str(value)).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    # Thousands separators only — a comma decimal separator is not used in these
    # exports, and stripping them is safer than guessing per row.
    text = text.replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _parse_currency(value) -> Currency | None:
    if not value:
        return None
    text = str(value).strip().upper()
    aliases = {"NIS": Currency.ILS, "SHEKEL": Currency.ILS, "₪": Currency.ILS, "$": Currency.USD}
    if text in aliases:
        return aliases[text]
    try:
        return Currency(text)
    except ValueError:
        return None
