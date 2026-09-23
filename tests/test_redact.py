from app.redact import normalise, redact


def test_redacts_iban_and_card():
    note = "TRANSFER TO PS92PALS000000000400123456789 CARD 4111 1111 1111 1111"
    out = redact(note)
    assert "PS92PALS000000000400123456789" not in out
    assert "4111" not in out
    assert "[IBAN]" in out


def test_redacts_long_reference_numbers():
    assert "3352119" not in redact("POS 3352119")


def test_keeps_merchant_text_including_arabic():
    assert "سوبر ماركت الأمل" in redact("سوبر ماركت الأمل 884213")


def test_short_numbers_survive():
    # A ₪3 fare must not be scrubbed away with the reference numbers.
    assert "3" in redact("TAXI 3")


def test_normalise_collapses_reference_numbers():
    # Same shop, different receipt number, one grouping key.
    assert normalise("سوبر ماركت الأمل 884213") == normalise("سوبر ماركت الأمل 991204")


def test_normalise_is_case_insensitive():
    assert normalise("Jawwal Top-Up") == normalise("JAWWAL TOP UP")
