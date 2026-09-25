import asyncio

import httpx
import pytest

from app.notes import NoteCleaner
from app.translate.base import NoopTranslator
from app.translate.openrouter import OpenRouterTranslator, needs_translation


def _translator(handler) -> OpenRouterTranslator:
    t = OpenRouterTranslator(api_key="test-key")
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai"
    )
    return t


def _reply(text: str):
    return lambda request: httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


# --- translation -------------------------------------------------------------


def test_detects_arabic():
    assert needs_translation("مياه - تانكر")
    assert not needs_translation("JAWWAL TOP UP")


def test_english_note_skips_the_call():
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _reply("x")(request)

    out = asyncio.run(_translator(handler).to_english("JAWWAL TOP UP"))
    assert out == "JAWWAL TOP UP"
    assert not called, "an English note should not cost a translation call"


def test_translates_arabic_note():
    assert asyncio.run(_translator(_reply("Water tanker")).to_english("مياه - تانكر")) == (
        "Water tanker"
    )


def test_failure_falls_back_to_the_original():
    handler = lambda request: httpx.Response(500)
    assert asyncio.run(_translator(handler).to_english("مياه")) == "مياه"


def test_malformed_response_falls_back():
    handler = lambda request: httpx.Response(200, json={"unexpected": True})
    assert asyncio.run(_translator(handler).to_english("مياه")) == "مياه"


def test_empty_translation_falls_back():
    assert asyncio.run(_translator(_reply("  ")).to_english("مياه")) == "مياه"


def test_missing_key_is_a_clear_error(monkeypatch):
    """Forced empty rather than assumed empty: a developer may well have
    OPENROUTER_API_KEY in their own environment, and this is about the message
    someone sees when there is genuinely no key."""
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterTranslator(api_key="")


# --- the cleaning pipeline ---------------------------------------------------


def test_clean_produces_a_tidy_vendor_name():
    note = asyncio.run(
        NoteCleaner(_translator(_reply("Al Amal Supermarket"))).clean(
            "POS بطاقة سوبر ماركت الأمل 884213"
        )
    )
    assert note.vendor_name == "Al Amal Supermarket"
    assert note.translated
    assert not note.is_filler_only


def test_clean_redacts_before_translating():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return _reply("Transfer")(request)

    asyncio.run(
        NoteCleaner(_translator(handler)).clean("حوالة PS92PALS000000000400123456789")
    )
    assert "PS92PALS000000000400123456789" not in seen["body"]


def test_clean_keeps_the_raw_note_untouched():
    raw = "POS بطاقة سوبر ماركت الأمل 884213"
    note = asyncio.run(NoteCleaner(_translator(_reply("Al Amal Supermarket"))).clean(raw))
    assert note.raw == raw


def test_filler_only_note_yields_no_vendor():
    note = asyncio.run(NoteCleaner(NoopTranslator()).clean("POS 3352119"))
    assert note.is_filler_only
    assert note.vendor_name == ""
    # The classifier still gets something to work with rather than an empty string.
    assert note.for_classifier


def test_english_note_needs_no_translator():
    note = asyncio.run(NoteCleaner(NoopTranslator()).clean("POS JAWWAL TOP UP 1234"))
    assert note.vendor_name == "Jawwal Top Up"
    assert not note.translated


def test_repeat_notes_are_cleaned_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _reply("Al Amal Supermarket")(request)

    cleaner = NoteCleaner(_translator(handler))

    async def run():
        # Same shop, different receipt numbers, asked for concurrently.
        await asyncio.gather(
            cleaner.clean("بطاقة سوبر ماركت الأمل 884213"),
            cleaner.clean("بطاقة سوبر ماركت الأمل 991204"),
            cleaner.clean("بطاقة سوبر ماركت الأمل 112233"),
        )

    asyncio.run(run())
    assert calls["n"] == 1
