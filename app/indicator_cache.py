from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional, Tuple

# Configuration and concurrency
from .config import INDICATOR_RING_SIZE
from .concurrency import pair_locks


class IndicatorCache:
    """Async-safe in-memory indicator cache with ring buffers per (symbol, timeframe).

    Design rules (per REARCHITECTING):
    - Single source of truth for indicator values across services (alerts/WS/debug).
    - Closed-bar values only should be populated by the indicators pipeline.
    - Use deques with fixed maxlen (ring buffers) for small memory footprint.

    Concurrency:
    - Uses the global keyed lock manager with a distinct prefix to avoid deadlocks
      with other services acquiring pair locks. Key format: "ind:{symbol}:{timeframe}".
    - All accessors are async and guarded to ensure consistency.

    Storage layout:
    - RSI:      (symbol -> timeframe -> period -> deque[(ts_ms, value)])
    - EMA:      (symbol -> timeframe -> period -> deque[(ts_ms, value)])
    - MACD:     (symbol -> timeframe -> (fast,slow,signal) -> deque[(ts_ms, macd, sig, hist)])
    """

    def __init__(self, ring_size: int = INDICATOR_RING_SIZE) -> None:
        self._ring_size = int(ring_size)
        # Nested dictionaries for each indicator family
        self._rsi: Dict[str, Dict[str, Dict[int, Deque[Tuple[int, float]]]]] = {}
        self._ema: Dict[str, Dict[str, Dict[int, Deque[Tuple[int, float]]]]] = {}
        self._macd: Dict[
            str,
            Dict[str, Dict[Tuple[int, int, int], Deque[Tuple[int, float, float, float]]]],
        ] = {}

    # -----------------------------
    # Helpers
    # -----------------------------
    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    @staticmethod
    def _lock_key(symbol: str, timeframe: str) -> str:
        # Prevent collisions with other services using pair locks
        return f"ind:{symbol}:{timeframe}"

    # -----------------------------
    # Update APIs
    # -----------------------------
    async def update_rsi(
        self,
        symbol: str,
        timeframe: str,
        period: int,
        value: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        """Append latest closed-bar RSI value to the ring for (symbol,timeframe,period)."""
        lock_key = self._lock_key(symbol, timeframe)
        async with pair_locks.acquire(lock_key):
            store_tf = self._rsi.setdefault(symbol, {}).setdefault(timeframe, {})
            dq = store_tf.get(period)
            if dq is None:
                dq = deque(maxlen=self._ring_size)
                store_tf[period] = dq
            dq.append((ts_ms or self._now_ms(), float(value)))

    async def update_ema(
        self,
        symbol: str,
        timeframe: str,
        period: int,
        value: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        """Append latest closed-bar EMA value to the ring for (symbol,timeframe,period)."""
        lock_key = self._lock_key(symbol, timeframe)
        async with pair_locks.acquire(lock_key):
            store_tf = self._ema.setdefault(symbol, {}).setdefault(timeframe, {})
            dq = store_tf.get(period)
            if dq is None:
                dq = deque(maxlen=self._ring_size)
                store_tf[period] = dq
            dq.append((ts_ms or self._now_ms(), float(value)))

    async def update_macd(
        self,
        symbol: str,
        timeframe: str,
        fast: int,
        slow: int,
        signal: int,
        macd_value: float,
        signal_value: float,
        hist_value: float,
        ts_ms: Optional[int] = None,
    ) -> None:
        """Append latest closed-bar MACD triplet to the ring for (symbol,timeframe,params)."""
        lock_key = self._lock_key(symbol, timeframe)
        params = (int(fast), int(slow), int(signal))
        async with pair_locks.acquire(lock_key):
            store_tf = self._macd.setdefault(symbol, {}).setdefault(timeframe, {})
            dq = store_tf.get(params)
            if dq is None:
                dq = deque(maxlen=self._ring_size)
                store_tf[params] = dq
            dq.append(
                (
                    ts_ms or self._now_ms(),
                    float(macd_value),
                    float(signal_value),
                    float(hist_value),
                )
            )

    # -----------------------------
    # Get APIs (latest)
    # -----------------------------
    async def get_latest_rsi(
        self, symbol: str, timeframe: str, period: int
    ) -> Optional[Tuple[int, float]]:
        """Return (ts_ms, value) or None if not available."""
        lock_key = self._lock_key(symbol, timeframe)
        async with pair_locks.acquire(lock_key):
            dq = (
                self._rsi.get(symbol, {})
                .get(timeframe, {})
                .get(int(period))
            )
            if dq and len(dq) > 0:
                return dq[-1]
            return None

    async def get_latest_ema(
        self, symbol: str, timeframe: str, period: int
    ) -> Optional[Tuple[int, float]]:
        """Return (ts_ms, value) or None if not available."""
        lock_key = self._lock_key(symbol, timeframe)
        async with pair_locks.acquire(lock_key):
            dq = (
                self._ema.get(symbol, {})
                .get(timeframe, {})
                .get(int(period))
            )
            if dq and len(dq) > 0:
                return dq[-1]
            return None

    async def get_latest_macd(
        self, symbol: str, timeframe: str, fast: int, slow: int, signal: int
    ) -> Optional[Tuple[int, float, float, float]]:
        """Return (ts_ms, macd, signal, hist) or None if not available."""
        lock_key = self._lock_key(symbol, timeframe)
        params = (int(fast), int(slow), int(signal))
        async with pair_locks.acquire(lock_key):
            dq = (
                self._macd.get(symbol, {})
                .get(timeframe, {})
                .get(params)
            )
            if dq and len(dq) > 0:
                return dq[-1]
            return None

    # -----------------------------
    # Misc
    # -----------------------------
    @property
    def ring_size(self) -> int:
        return self._ring_size

    def set_ring_size(self, new_size: int) -> None:
        """Set a new ring size for future deques. Existing deques keep their maxlen."""
        self._ring_size = int(new_size)


# Global singleton
indicator_cache = IndicatorCache()


__all__ = [
    "indicator_cache",
    "IndicatorCache",
]


