from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from .config import INDICATOR_RING_SIZE
from .concurrency import pair_locks


class CurrencyStrengthCache:
    """Async-safe in-memory cache for currency strength snapshots per timeframe.

    Storage layout:
      - timeframe -> deque[(ts_ms, { "USD": float, "EUR": float, ... })]

    Notes:
      - Snapshots are computed on closed bars only and represent the latest available
        aggregate across all supported pairs for the given timeframe.
      - The timestamp corresponds to the most recent closed-bar time across the contributing pairs.
    """

    def __init__(self, ring_size: int = INDICATOR_RING_SIZE) -> None:
        self._ring_size: int = int(ring_size)
        self._store: Dict[str, Deque[Tuple[int, Dict[str, float]]]] = {}

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    @staticmethod
    def _lock_key(timeframe: str) -> str:
        return f"curstr:{timeframe}"

    async def update(self, timeframe: str, values: Dict[str, float], ts_ms: Optional[int] = None) -> None:
        """Append a new currency strength snapshot for the timeframe.

        Args:
            timeframe: One of 5M, 15M, 30M, 1H, 4H, 1D, 1W.
            values: Mapping of currency code -> strength (0-100 style scale).
            ts_ms: Timestamp of the contributing closed bar set (epoch milliseconds). If None, uses now.
        """
        lock_key = self._lock_key(timeframe)
        async with pair_locks.acquire(lock_key):
            dq = self._store.get(timeframe)
            if dq is None:
                dq = deque(maxlen=self._ring_size)
                self._store[timeframe] = dq
            dq.append((int(ts_ms or self._now_ms()), dict(values)))

    async def latest(self, timeframe: str) -> Optional[Tuple[int, Dict[str, float]]]:
        """Return the latest (ts_ms, values) snapshot for a timeframe, or None if unavailable."""
        lock_key = self._lock_key(timeframe)
        async with pair_locks.acquire(lock_key):
            dq = self._store.get(timeframe)
            if dq and len(dq) > 0:
                ts_ms, values = dq[-1]
                return int(ts_ms), dict(values)
            return None

    async def recent(self, timeframe: str, count: int) -> Optional[List[Tuple[int, Dict[str, float]]]]:
        """Return the last N snapshots for timeframe in chronological order, or None if empty."""
        if count <= 0:
            return []
        lock_key = self._lock_key(timeframe)
        async with pair_locks.acquire(lock_key):
            dq = self._store.get(timeframe)
            if not dq or len(dq) == 0:
                return None
            start_index = max(0, len(dq) - int(count))
            # Return copies to prevent mutation
            return [(int(ts), dict(vals)) for ts, vals in list(dq)[start_index:]]


# Global singleton
currency_strength_cache = CurrencyStrengthCache()


__all__ = [
    "currency_strength_cache",
    "CurrencyStrengthCache",
]


