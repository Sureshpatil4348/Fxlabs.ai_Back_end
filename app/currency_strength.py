from __future__ import annotations

from math import log
from typing import Dict, List, Optional, Tuple

from .models import Timeframe
from .mt5_utils import canonicalize_symbol, ensure_symbol_selected, get_ohlc_data


SUPPORTED_FIAT: List[str] = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]


def _parse_base_quote(symbol: str) -> Optional[Tuple[str, str]]:
    """Parse base and quote currencies from an MT5 symbol string.

    Assumes standard FX symbol format of 6 letters plus optional trailing 'm' broker suffix,
    e.g., 'EURUSDm' -> ('EUR','USD'). Returns None for non-standard or non-fiat pairs.
    """
    try:
        s = canonicalize_symbol(symbol)
        if not s:
            return None
        core = s[:-1] if s.endswith("m") else s
        if len(core) < 6:
            return None
        base = core[:3]
        quote = core[3:6]
        if base not in SUPPORTED_FIAT or quote not in SUPPORTED_FIAT:
            return None
        return base, quote
    except Exception:
        return None


def _pick_close(b) -> Optional[float]:
    try:
        # Prefer mid close; use close field
        v = getattr(b, "close", None)
        return float(v) if v is not None else None
    except Exception:
        return None


async def compute_currency_strength_for_timeframe(
    timeframe: Timeframe,
    symbols: List[str],
) -> Optional[Tuple[int, Dict[str, float]]]:
    """Compute currency strength snapshot for a timeframe using ROC (log returns) on closed bars.

    Returns (ts_ms, strength_map) where strength_map values are normalized to the range [-100, 100]
    with 0 as neutral. Returns None if insufficient data.
    """
    # Accumulators
    contrib_sum: Dict[str, float] = {c: 0.0 for c in SUPPORTED_FIAT}
    contrib_count: Dict[str, int] = {c: 0 for c in SUPPORTED_FIAT}
    latest_ts_ms: int = 0

    for sym in symbols:
        # Skip non-fiat pairs
        pq = _parse_base_quote(sym)
        if not pq:
            continue
        base, quote = pq
        try:
            ensure_symbol_selected(sym)
        except Exception:
            # Best-effort; continue
            pass
        bars = get_ohlc_data(sym, timeframe, 5)
        if not bars:
            continue
        closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
        if len(closed_bars) < 2:
            continue
        prev_bar = closed_bars[-2]
        last_bar = closed_bars[-1]
        p0 = _pick_close(prev_bar)
        p1 = _pick_close(last_bar)
        if p0 is None or p1 is None or p0 <= 0.0 or p1 <= 0.0:
            continue
        try:
            r = log(p1 / p0)
        except Exception:
            continue
        contrib_sum[base] += r
        contrib_count[base] += 1
        contrib_sum[quote] -= r
        contrib_count[quote] += 1
        try:
            ts = int(getattr(last_bar, "time", 0))
            if ts > latest_ts_ms:
                latest_ts_ms = ts
        except Exception:
            pass

    # If no contributions at all, return neutral when nothing computed
    any_contrib = any(contrib_count[c] > 0 for c in SUPPORTED_FIAT)
    if not any_contrib:
        return None

    # Average per currency, scale around 50, clamp to [20,80]
    raw_scores: Dict[str, float] = {}
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    for c in SUPPORTED_FIAT:
        if contrib_count[c] > 0:
            avg = contrib_sum[c] / float(contrib_count[c])
            v = 50.0 + (avg * 200.0)
            v = 80.0 if v > 80.0 else (20.0 if v < 20.0 else v)
            raw_scores[c] = v
            min_val = v if (min_val is None or v < min_val) else min_val
            max_val = v if (max_val is None or v > max_val) else max_val

    # Linear normalize real scores to [10,90] preserving rank
    normalized: Dict[str, float] = {}
    if min_val is not None and max_val is not None and max_val > min_val:
        span = max_val - min_val
        for c, v in raw_scores.items():
            normalized[c] = 10.0 + (v - min_val) * (80.0 / span)
    else:
        for c, v in raw_scores.items():
            normalized[c] = 50.0

    # Fill missing currencies to neutral 50 and final clamp to [10,90]
    for c in SUPPORTED_FIAT:
        if c not in normalized:
            normalized[c] = 50.0
        else:
            v = normalized[c]
            if v < 10.0:
                normalized[c] = 10.0
            elif v > 90.0:
                normalized[c] = 90.0

    # Map normalized [10,90] to [-100,100] with 0 as neutral.
    # Linear mapping: (-100) <= (val - 50)*2.5 <= 100.
    scaled: Dict[str, float] = {}
    for c, v in normalized.items():
        nv = (v - 50.0) * 2.5
        # Safety clamp
        if nv < -100.0:
            nv = -100.0
        elif nv > 100.0:
            nv = 100.0
        scaled[c] = nv

    # Ensure timestamp
    if latest_ts_ms <= 0:
        latest_ts_ms = 0

    return latest_ts_ms, scaled


__all__ = [
    "compute_currency_strength_for_timeframe",
    "SUPPORTED_FIAT",
]

