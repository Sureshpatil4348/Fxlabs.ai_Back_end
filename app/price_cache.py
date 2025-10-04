from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Any

from .concurrency import pair_locks


class PriceCache:
    """Async-safe in-memory cache for the latest price snapshot per symbol.

    Stored shape per symbol:
    {
      "symbol": str,
      "time": int,            # epoch ms
      "time_iso": str,        # ISO-8601 UTC
      "bid": Optional[float],
      "ask": Optional[float],
      "daily_change_pct": Optional[float]
    }
    """

    def __init__(self) -> None:
        self._latest: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _lock_key(symbol: str) -> str:
        # Distinct namespace to avoid collisions with other services using pair_locks
        return f"price:{symbol}"

    async def update(
        self,
        symbol: str,
        *,
        time_ms: int,
        time_iso: Optional[str],
        bid: Optional[float],
        ask: Optional[float],
        daily_change_pct: Optional[float],
    ) -> None:
        lock_key = self._lock_key(symbol)
        async with pair_locks.acquire(lock_key):
            self._latest[symbol] = {
                "symbol": symbol,
                "time": int(time_ms),
                "time_iso": time_iso
                or datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc).isoformat(),
                "bid": float(bid) if bid is not None else None,
                "ask": float(ask) if ask is not None else None,
                "daily_change_pct": float(daily_change_pct)
                if daily_change_pct is not None
                else None,
            }

    async def get_latest(self, symbol: str) -> Optional[Dict[str, Any]]:
        lock_key = self._lock_key(symbol)
        async with pair_locks.acquire(lock_key):
            snap = self._latest.get(symbol)
            if snap is None:
                return None
            # Return a shallow copy to avoid external mutation
            return dict(snap)


# Global singleton
price_cache = PriceCache()


__all__ = [
    "price_cache",
    "PriceCache",
]


