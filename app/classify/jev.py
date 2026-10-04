"""Jev (TypeSafe System One) backend.

One request per unique note, carrying three questions. Jev evaluates the
questions in parallel, so asking for the category, whether the note is a
merchant name at all, and how reducible the spend is costs one round trip rather
than three.

We call the REST endpoint through httpx rather than the typesafe-sdk package so
that the request and response shapes are visible and testable here, and so the
retry and timeout behaviour is ours. `pip install typesafe-sdk` is the
alternative if you would rather have the typed helpers.
"""

import asyncio

import httpx

from app.config import settings
from app.redact import redact
from app.taxonomy import CATEGORIES, REDUCIBILITY_SCALE, is_valid_category

from .base import Decision

_ENDPOINT = "/v1/systemone"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class JevClassifier:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or settings.typesafe_api_key
        if not key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is not set. Set CLASSIFIER_BACKEND=mock to run "
                "without it."
            )
        self._model = model or settings.typesafe_model
        self._client = httpx.AsyncClient(
            base_url=settings.typesafe_base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    def _payload(self, note: str, amount: float, currency: str) -> dict:
        direction = "received" if amount > 0 else "paid out"
        state = (
            "A bank transaction from a household in Gaza.\n"
            f"Amount: {abs(amount):.2f} {currency} ({direction}).\n"
            f"Statement note: {redact(note)}"
        )
        return {
            "state": state,
            "model": self._model,
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": (
                        "Which household expense category does this transaction "
                        "belong to? The note may be in Arabic, in transliterated "
                        "Arabic, or in English."
                    ),
                    "criteria": CATEGORIES,
                },
                "is_merchant": {
                    "type": "noul",
                    "instructions": (
                        "The note names a real shop, vendor, person or service "
                        "rather than being only a bank reference code or filler."
                    ),
                },
                "reducibility": {
                    "type": "score",
                    "instructions": (
                        "How far could this household realistically reduce this "
                        "spending without harm?"
                    ),
                    "criteria": REDUCIBILITY_SCALE,
                },
            },
        }

    async def classify(self, note: str, amount: float, currency: str) -> Decision:
        data = await self._post(self._payload(note, amount, currency))
        answers = data.get("answers", {})

        cat = answers.get("category", {})
        category = cat.get("choice")
        if not is_valid_category(category or ""):
            # Jev cannot return an out-of-schema label, so this means a malformed
            # or error response. Fail to review rather than guessing.
            return Decision("other", 0.0, {})

        return Decision(
            category=category,
            confidence=float(cat.get("confidence", 0.0)),
            probabilities={k: float(v) for k, v in (cat.get("probabilities") or {}).items()},
            is_merchant=_maybe_float(answers.get("is_merchant", {}).get("noul")),
            reducibility=_level(answers.get("reducibility", {})),
        )

    async def _post(self, payload: dict, attempts: int = 3) -> dict:
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.post(_ENDPOINT, json=payload)
                if resp.status_code in _RETRY_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last = exc
                if attempt < attempts - 1:
                    # Connectivity in Gaza is intermittent; back off rather than
                    # dropping the whole import batch.
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"Jev request failed after {attempts} attempts") from last

    async def aclose(self) -> None:
        await self._client.aclose()


def _maybe_float(value) -> float | None:
    return None if value is None else float(value)


def _level(answer: dict) -> int | None:
    # A score is the probability-weighted mean of the levels (0.69 when level 1
    # has 0.68), so truncating it drops a level. Take the most likely level.
    probs = answer.get("probabilities")
    if probs:
        return int(max(probs, key=probs.get))
    score = answer.get("score")
    return None if score is None else round(score)
