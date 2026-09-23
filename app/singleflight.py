"""Run one coroutine per key, no matter how many callers ask at once.

Importing a statement fans out over every row concurrently, and the same vendor
appears many times in it. Without this, thirty identical grocer lines become
thirty translation calls and thirty classification calls that all started before
the first one finished.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class SingleFlight(Generic[K, V]):
    def __init__(self) -> None:
        self._done: dict[K, V] = {}
        self._inflight: dict[K, asyncio.Future[V]] = {}
        self._lock = asyncio.Lock()

    async def do(self, key: K, fn: Callable[[], Awaitable[V]]) -> V:
        async with self._lock:
            if key in self._done:
                return self._done[key]
            pending = self._inflight.get(key)
            if pending is None:
                future: asyncio.Future[V] = asyncio.get_running_loop().create_future()
                self._inflight[key] = future

        if pending is not None:
            # Awaited outside the lock, or the caller doing the work could never
            # reacquire it to store the result.
            return await asyncio.shield(pending)

        try:
            value = await fn()
        except Exception as exc:
            async with self._lock:
                self._inflight.pop(key, None)
            future.set_exception(exc)
            raise
        else:
            async with self._lock:
                self._done[key] = value
                self._inflight.pop(key, None)
            future.set_result(value)
            return value

    def __contains__(self, key: K) -> bool:
        return key in self._done

    @property
    def size(self) -> int:
        return len(self._done)
