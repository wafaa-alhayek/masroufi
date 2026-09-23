import asyncio

import httpx
import pytest

from app.classify.base import Decision
from app.classify.cache import CachingClassifier
from app.classify.translating import TranslatingClassifier
from app.translate.base import NoopTranslator
from app.translate.openrouter import OpenRouterTranslator, needs_translation


def _translator(handler) -> OpenRouterTranslator:
    t = OpenRouterTranslator(api_key="test-key")
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai"
    )
    return t


def _reply(text: str):
    return lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": text}}]}
    )


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
    out = asyncio.run(_translator(_reply("Water tanker")).to_english("مياه - تانكر"))
    assert out == "Water tanker"


def test_failure_falls_back_to_the_original_note():
    handler = lambda request: httpx.Response(500)
    out = asyncio.run(_translator(handler).to_english("مياه - تانكر"))
    assert out == "مياه - تانكر", "a failed translation must not fail the import"


def test_malformed_response_falls_back():
    handler = lambda request: httpx.Response(200, json={"unexpected": True})
    assert asyncio.run(_translator(handler).to_english("مياه")) == "مياه"


def test_empty_translation_falls_back():
    assert asyncio.run(_translator(_reply("  ")).to_english("مياه")) == "مياه"


def test_note_is_redacted_before_translation():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return _reply("Transfer")(request)

    class _Sink:
        async def classify(self, note, amount, currency):
            return Decision("other", 0.5, {})

        async def aclose(self):
            return None

    chain = TranslatingClassifier(_Sink(), _translator(handler))
    asyncio.run(chain.classify("حوالة PS92PALS000000000400123456789", -100.0, "ILS"))
    assert "PS92PALS000000000400123456789" not in seen["body"]


def test_translated_text_reaches_the_classifier():
    seen = {}

    class _Sink:
        async def classify(self, note, amount, currency):
            seen["note"] = note
            return Decision("water", 0.9, {"water": 0.9})

        async def aclose(self):
            return None

    chain = TranslatingClassifier(_Sink(), _translator(_reply("Water tanker")))
    asyncio.run(chain.classify("مياه - تانكر", -60.0, "ILS"))
    assert seen["note"] == "Water tanker"


def test_cache_prevents_repeat_translations():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _reply("Al Amal Supermarket")(request)

    class _Sink:
        async def classify(self, note, amount, currency):
            return Decision("groceries", 0.9, {"groceries": 0.9})

        async def aclose(self):
            return None

    chain = CachingClassifier(TranslatingClassifier(_Sink(), _translator(handler)))

    async def run():
        # Same shop, different receipt numbers — one translation between them.
        await chain.classify("سوبر ماركت الأمل 884213", -145.5, "ILS")
        await chain.classify("سوبر ماركت الأمل 991204", -132.0, "ILS")

    asyncio.run(run())
    assert calls["n"] == 1


def test_noop_translator_is_transparent():
    assert asyncio.run(NoopTranslator().to_english("مياه")) == "مياه"


def test_missing_key_is_a_clear_error():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterTranslator(api_key="")
