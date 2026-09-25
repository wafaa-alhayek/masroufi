import asyncio

import httpx
import pytest

from app.classify.base import Decision
from app.classify.cache import CachingClassifier
from app.classify.jev import JevClassifier
from app.classify.mock import MockClassifier


def test_mock_classifies_arabic_notes():
    d = asyncio.run(MockClassifier().classify("مياه - تانكر", -60.0, "ILS"))
    assert d.category == "water"
    assert d.reducibility == 0


def test_unrecognised_note_goes_to_review():
    d = asyncio.run(MockClassifier().classify("MISC DEBIT 7781", -22.0, "ILS"))
    assert d.needs_review(threshold=0.70)


class _Counting:
    def __init__(self):
        self.calls = 0

    async def classify(self, note, amount, currency):
        self.calls += 1
        await asyncio.sleep(0)
        return Decision("groceries", 0.9, {"groceries": 0.9})

    async def aclose(self):
        return None


def test_cache_reuses_decision_for_repeat_vendor():
    inner = _Counting()
    cache = CachingClassifier(inner)

    async def run():
        # Same shop, different receipt numbers.
        await cache.classify("سوبر ماركت الأمل 884213", -145.5, "ILS")
        await cache.classify("سوبر ماركت الأمل 991204", -132.0, "ILS")

    asyncio.run(run())
    assert inner.calls == 1


def test_concurrent_identical_notes_make_one_call():
    inner = _Counting()
    cache = CachingClassifier(inner)

    async def run():
        await asyncio.gather(*(cache.classify("سوبر ماركت", -10.0, "ILS") for _ in range(20)))

    asyncio.run(run())
    assert inner.calls == 1


def test_direction_is_part_of_the_cache_key():
    inner = _Counting()
    cache = CachingClassifier(inner)

    async def run():
        await cache.classify("AHMED", -100.0, "ILS")
        await cache.classify("AHMED", 100.0, "ILS")

    asyncio.run(run())
    assert inner.calls == 2, "money out to a name is not the same as money in from it"


def test_jev_parses_documented_response_shape():
    """Pinned against the response shape in TypeSafe's quickstart docs."""
    payload = {
        "model": "jev-1.13.0",
        "answers": {
            "category": {
                "type": "choice",
                "choice": "water",
                "confidence": 0.78,
                "probabilities": {"water": 0.85, "groceries": 0.15},
            },
            "is_merchant": {"type": "noul", "noul": 1.0},
            "reducibility": {"type": "score", "score": 1.0, "confidence": 0.9},
        },
        "usage": {"input_tokens": 392, "output_tokens": 65},
    }

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        client = JevClassifier(api_key="test-key")
        client._client = httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai")
        try:
            return await client.classify("مياه - تانكر", -60.0, "ILS")
        finally:
            await client.aclose()

    d = asyncio.run(run())
    assert d.category == "water"
    assert d.confidence == 0.78
    assert d.is_merchant == 1.0
    assert d.reducibility == 1


def test_jev_sends_redacted_state():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"answers": {"category": {"choice": "other", "confidence": 0.5, "probabilities": {}}}},
        )

    async def run():
        client = JevClassifier(api_key="test-key")
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.typesafe.ai"
        )
        try:
            await client.classify("TRANSFER PS92PALS000000000400123456789", -100.0, "ILS")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert "PS92PALS000000000400123456789" not in seen["body"]


def test_malformed_response_falls_back_to_review():
    async def run():
        client = JevClassifier(api_key="test-key")
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"answers": {}})),
            base_url="https://api.typesafe.ai",
        )
        try:
            return await client.classify("anything", -1.0, "ILS")
        finally:
            await client.aclose()

    d = asyncio.run(run())
    assert d.confidence == 0.0
    assert d.needs_review(threshold=0.70)


def test_missing_api_key_is_a_clear_error(monkeypatch):
    """Forced empty rather than assumed empty, so the test does not break the day a
    developer puts a real key in their environment."""
    from app.config import settings

    monkeypatch.setattr(settings, "typesafe_api_key", "")
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        JevClassifier(api_key="")
