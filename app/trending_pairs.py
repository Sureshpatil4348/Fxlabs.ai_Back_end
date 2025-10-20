from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable

from .mt5_utils import get_daily_change_pct_bid, canonicalize_symbol, ensure_symbol_selected
import asyncio


class TrendingPairsCache:
    """Async-safe in-memory cache for trending pairs evaluated by daily % change.

    - Trending criterion: abs(daily_change_pct) >= threshold_pct
    - Stores last snapshot with evaluation metadata
    - Exposes atomic async getters
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._threshold_pct: float = 0.05  # default threshold in percent
        self._pairs: List[Tuple[str, float]] = []  # [(symbol, daily_change_pct)]
        self._last_updated: Optional[datetime] = None

    async def set_snapshot(self, *, threshold_pct: float, pairs: List[Tuple[str, float]]) -> None:
        async with self._lock:
            self._threshold_pct = float(threshold_pct)
            # Sort by absolute magnitude desc for presentation consistency
            self._pairs = sorted(list(pairs), key=lambda kv: abs(kv[1]), reverse=True)
            self._last_updated = datetime.now(timezone.utc)

    async def get_snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "threshold_pct": self._threshold_pct,
                "last_updated": self._last_updated.isoformat() if self._last_updated else None,
                "count": len(self._pairs),
                "pairs": [
                    {"symbol": sym, "daily_change_pct": float(dcp)} for sym, dcp in self._pairs
                ],
            }


trending_pairs_cache = TrendingPairsCache()


async def refresh_trending_pairs(symbols: List[str], *, threshold_pct: float = 0.05) -> Dict[str, Any]:
    """Compute and update trending pairs snapshot for the provided symbols.

    Returns the snapshot dict stored in the cache.
    """
    logger = logging.getLogger("obs.trending")
    t0 = datetime.now(timezone.utc)
    symbols_canon = []
    for s in symbols:
        try:
            symbols_canon.append(canonicalize_symbol(s))
        except Exception:
            continue

    trending: List[Tuple[str, float]] = []
    errs: int = 0
    for sym in symbols_canon:
        try:
            # Ensure MT5 symbol is ready and compute dcp (Bid-based)
            try:
                await asyncio.to_thread(ensure_symbol_selected, sym)
            except Exception:
                pass
            dcp = await asyncio.to_thread(get_daily_change_pct_bid, sym)
            if dcp is None:
                continue
            if abs(float(dcp)) >= float(threshold_pct):
                trending.append((sym, float(dcp)))
        except Exception:
            errs += 1
            continue

    await trending_pairs_cache.set_snapshot(threshold_pct=float(threshold_pct), pairs=trending)
    t1 = datetime.now(timezone.utc)
    try:
        elapsed_ms = int((t1 - t0).total_seconds() * 1000)
        logger.info(
            "🔎 trending_eval | threshold_pct=%.4f pairs=%d trending=%d errors=%d duration_ms=%d",
            float(threshold_pct),
            len(symbols_canon),
            len(trending),
            errs,
            elapsed_ms,
        )
    except Exception:
        pass
    return await trending_pairs_cache.get_snapshot()


async def trending_pairs_scheduler(
    symbols: List[str],
    *,
    threshold_pct: float = 0.05,
    broadcast: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> None:
    """Run trending pairs evaluation at startup and every hour aligned to the hour boundary.

    If `broadcast` is provided, it will be awaited with the latest snapshot after each refresh.
    """
    logger = logging.getLogger("obs.trending")
    try:
        # Initial population on startup (best-effort)
        snap = await refresh_trending_pairs(symbols, threshold_pct=threshold_pct)
        if broadcast:
            try:
                await broadcast(snap)
            except Exception:
                pass

        # Align to next top-of-hour boundary
        def next_hour(dt: datetime) -> datetime:
            return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        next_run = next_hour(datetime.now(timezone.utc))
        while True:
            now = datetime.now(timezone.utc)
            delay = max((next_run - now).total_seconds(), 0.05)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

            # Recompute and optionally broadcast
            snap = await refresh_trending_pairs(symbols, threshold_pct=threshold_pct)
            if broadcast:
                try:
                    await broadcast(snap)
                except Exception:
                    pass

            # Schedule next run
            next_run = next_hour(datetime.now(timezone.utc))
    except asyncio.CancelledError:
        return
    except Exception as e:
        try:
            logger.error("❌ trending_scheduler_error: %s", str(e))
        except Exception:
            pass

