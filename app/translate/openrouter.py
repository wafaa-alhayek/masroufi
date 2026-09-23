"""Translate a statement note to English via OpenRouter.

Only called for notes that actually contain non-Latin script, so an English-only
statement costs nothing extra.

The note is redacted before it gets here (see CachingClassifier → TranslatingClassifier
ordering in app/classify/__init__.py), because this adds a second third party to
the path and it should see no more than the decision model does.
"""

import asyncio
import re

import httpx

from app.config import settings

_ENDPOINT = "/api/v1/chat/completions"
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Arabic, Arabic Supplement, Arabic Extended-A, Arabic Presentation Forms.
_NON_LATIN = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

_SYSTEM = (
    "You translate bank transaction notes from a Palestinian bank into English. "
    "Reply with the English only, no quotes, no explanation, no more than 12 words. "
    "Keep shop and business names as recognisable names rather than translating "
    "them literally. If the note is already English, or is only a reference code, "
    "repeat it back unchanged. If you cannot tell what it means, repeat it back "
    "unchanged rather than guessing."
)


def needs_translation(text: str) -> bool:
    return bool(_NON_LATIN.search(text))


class OpenRouterTranslator:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or settings.openrouter_api_key
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Set TRANSLATE_NOTES=false to run "
                "without the translation step."
            )
        self._model = model or settings.openrouter_model
        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {key}",
                "X-Title": "Masroufi",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def to_english(self, text: str) -> str:
        if not text.strip() or not needs_translation(text):
            return text

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text},
            ],
            "max_tokens": 40,
            "temperature": 0,
        }

        try:
            data = await self._post(payload)
        except RuntimeError:
            # A failed translation must not fail the import. Fall back to the
            # original note and let the classifier do what it can with it.
            return text

        try:
            translated = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError, TypeError):
            return text

        return translated or text

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
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"OpenRouter request failed after {attempts} attempts") from last

    async def aclose(self) -> None:
        await self._client.aclose()
