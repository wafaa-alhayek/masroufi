"""Cache decisions by normalised note.

Repeat vendors are the common case in a household statement, so the same note
gets classified once and reused. The in-flight map means that importing a
statement with fifty identical grocer lines makes one request, not fifty.

This is per-process and resets on restart. A persistent decision table is the
obvious next step once there is more than one worker.
"""

import asyncio

from app.redact import normalise

from .base import Classifier, Decision


class CachingClassifier:
    def __init__(self, inner: Classifier) -> None:
        self._inner = inner
        self._cache: dict[tuple[str, str, bool], Decision] = {}
        self._inflight: dict[tuple[str, str, bool], asyncio.Future[Decision]] = {}
        self._lock = asyncio.Lock()

    async def classify(self, note: str, amount: float, currency: str) -> Decision:
        # Direction is part of the key: the same counterparty name means a
        # different category depending on which way the money went.
        key = (normalise(note), currency, amount > 0)

        async with self._lock:
            if key in self._cache:
                return self._cache[key]
            pending = self._inflight.get(key)
            if pending is None:
                future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
                self._inflight[key] = future

        if pending is not None:
            # Someone else is already asking. Wait for their answer — outside the
            # lock, or the coroutine that is asking could never store its result.
            return await asyncio.shield(pending)

        try:
            decision = await self._inner.classify(note, amount, currency)
        except Exception as exc:
            async with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(exc)
            raise
        else:
            async with self._lock:
                self._cache[key] = decision
                self._inflight.pop(key, None)
            future.set_result(decision)
            return decision

    async def aclose(self) -> None:
        await self._inner.aclose()

    @property
    def size(self) -> int:
        return len(self._cache)
