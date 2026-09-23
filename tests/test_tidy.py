from app.tidy import display_name, is_filler_only, match_key, strip_filler


def test_strips_payment_network_wrapping():
    assert strip_filler("POS PURCHASE VISA AL AMAL SUPERMARKET 3352119") == (
        "AL AMAL SUPERMARKET"
    )


def test_strips_arabic_filler():
    assert strip_filler("عملية شراء بطاقة سوبر ماركت الأمل") == "سوبر ماركت الأمل"


def test_filler_only_notes_are_recognised():
    assert is_filler_only("POS 3352119")
    assert is_filler_only("MISC DEBIT 7781")
    assert is_filler_only("REF 99120384")
    assert not is_filler_only("POS AL AMAL 3352119")


def test_display_name_is_readable():
    assert display_name("POS AL AMAL SUPERMARKET 3352119") == "Al Amal Supermarket"


def test_display_name_leaves_arabic_alone():
    assert display_name("بطاقة سوبر ماركت الأمل 884213") == "سوبر ماركت الأمل"


def test_atm_is_filler_and_the_bank_is_the_vendor():
    assert display_name("ATM CAIRO AMMAN") == "Cairo Amman"


def test_currency_codes_stay_upper():
    assert display_name("EXCHANGE USD 4412") == "Exchange USD"


def test_redaction_placeholders_do_not_become_vendor_names():
    # The note reaches tidying already redacted, so "[NUM]" must not survive.
    assert display_name("POS AL AMAL [NUM]") == "Al Amal"
    assert is_filler_only("POS [NUM]")


def test_a_shop_named_phone_keeps_its_name():
    # Only the bracketed placeholder is stripped, not the bare word.
    assert display_name("PHONE SHOP GAZA") == "Phone Shop Gaza"


def test_match_key_is_order_insensitive():
    # The same shop written two ways is one vendor.
    assert match_key("AL AMAL SUPERMARKET") == match_key("SUPERMARKET AL AMAL")


def test_match_key_ignores_reference_numbers_and_case():
    assert match_key("POS Al Amal Supermarket 884213") == match_key(
        "VISA AL AMAL SUPERMARKET 991204"
    )


def test_distinct_shops_do_not_collide():
    assert match_key("AL AMAL SUPERMARKET") != match_key("AL SALAM SUPERMARKET")


def test_single_characters_are_dropped_as_noise():
    assert strip_filler("A B AL AMAL") == "AL AMAL"
