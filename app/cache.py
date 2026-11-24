"""A small in-process TTL cache.

Good enough for one replica. With several replicas the same interface can be
backed by Redis, so every pod shares one set of upstream lookups.
"""

from __future__ import annotations

import time
from typing import Any

_MISSING = object()


class TTLCache:
    def __init__(self, max_items: int = 10_000) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._max = max_items

    def get(self, key: str) -> Any:
        hit = self._items.get(key, _MISSING)
        if hit is _MISSING:
            return _MISSING
        expires, value = hit
        if expires < time.monotonic():
            self._items.pop(key, None)
            return _MISSING
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._items) >= self._max:
            self._evict()
        self._items[key] = (time.monotonic() + ttl, value)

    def _evict(self) -> None:
        now = time.monotonic()
        for key in [k for k, (exp, _) in self._items.items() if exp < now]:
            del self._items[key]
        while len(self._items) >= self._max:
            self._items.pop(next(iter(self._items)))


MISSING = _MISSING
