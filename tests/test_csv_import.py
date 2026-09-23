from datetime import date
from pathlib import Path

import pytest

from app.importers.csv_import import ImportError_, parse_csv
from app.models import Currency

SAMPLE = Path(__file__).parent.parent / "data" / "sample_statement.csv"


def test_parses_sample_statement():
    rows = parse_csv(SAMPLE.read_bytes(), batch="test")
    assert len(rows) == 31
    assert rows[0].booked_on == date(2026, 1, 3)
    assert rows[0].amount == 1200.00, "a credit should be positive"
    assert rows[1].amount == -145.50, "a debit should be negative"
    assert rows[0].currency is Currency.ILS


def test_amount_column_layout():
    raw = b"Date,Details,Amount\n2026-01-05,Water tanker,-60.00\n"
    rows = parse_csv(raw, batch="test")
    assert rows[0].amount == -60.00
    assert rows[0].note == "Water tanker"


def test_arabic_headers():
    raw = "التاريخ,البيان,مدين,دائن\n2026-01-05,مياه,60.00,\n".encode()
    rows = parse_csv(raw, batch="test")
    assert rows[0].amount == -60.00


def test_thousands_separator_and_parentheses_negative():
    raw = b'Date,Details,Amount\n2026-01-05,Rent,"(1,200.00)"\n'
    assert parse_csv(raw, batch="test")[0].amount == -1200.00


def test_cp1256_encoded_file():
    raw = "Date,Details,Amount\n2026-01-05,مياه,-60\n".encode("cp1256")
    assert parse_csv(raw, batch="test")[0].note == "مياه"


def test_footer_rows_are_skipped_not_fatal():
    raw = b"Date,Details,Amount\n2026-01-05,Water,-60.00\nTotal,,-60.00\n"
    assert len(parse_csv(raw, batch="test")) == 1


def test_missing_note_column_is_rejected():
    with pytest.raises(ImportError_, match="note"):
        parse_csv(b"Date,Amount\n2026-01-05,-60.00\n", batch="test")


def test_no_usable_rows_is_rejected():
    with pytest.raises(ImportError_):
        parse_csv(b"Date,Details,Amount\nTotal,,0\n", batch="test")
