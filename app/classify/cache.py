"""Cache decisions by cleaned note.

Repeat vendors are the common case in a household statement, so the same vendor
is classified once and reused. Concurrency is handled by SingleFlight: importing
a statement with fifty identical grocer lines makes one request, not fifty.

This is per-process and resets on restart. The vendor table is the durable
version of the same idea — once a vendor's category is confirmed, the classifier
is not consulted at all.
"""

from app.redact import normalise
from app.singleflight import SingleFlight

from .base import Classifier, Decision


class CachingClassifier:
    def __init__(self, inner: Classifier) -> None:
        self._inner = inner
        self._flight: SingleFlight[tuple[str, str, bool], Decision] = SingleFlight()

    async def classify(self, note: str, amount: float, currency: str) -> Decision:
        # Direction is part of the key: the same counterparty name means a
        # different category depending on which way the money went.
        key = (normalise(note), currency, amount > 0)
        return await self._flight.do(key, lambda: self._inner.classify(note, amount, currency))

    async def aclose(self) -> None:
        await self._inner.aclose()

    @property
    def size(self) -> int:
        return self._flight.size
