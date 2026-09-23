"""Translate a note to English, then classify it.

Sits between the cache and the decision model, so a translation is paid for once
per unique note and never again — the same grocer appearing forty times is one
translation and one classification.

The note is redacted before translation, not after, so the translation service
sees no account or card numbers either.
"""

from app.redact import redact
from app.translate.base import Translator

from .base import Classifier, Decision


class TranslatingClassifier:
    def __init__(self, inner: Classifier, translator: Translator) -> None:
        self._inner = inner
        self._translator = translator

    async def classify(self, note: str, amount: float, currency: str) -> Decision:
        english = await self._translator.to_english(redact(note))
        return await self._inner.classify(english, amount, currency)

    async def aclose(self) -> None:
        await self._translator.aclose()
        await self._inner.aclose()
