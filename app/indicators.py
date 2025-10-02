from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Reuse existing RSI utilities for parity with prior services
from .rsi_utils import calculate_rsi_series as rsi_series_wilder
from .rsi_utils import calculate_rsi_latest as rsi_latest_wilder
from .rsi_utils import closed_closes as closed_only_closes


def ema_series(closes: Sequence[float], period: int) -> List[float]:
    """Return EMA series aligned to closes using standard smoothing.

    - Seed: simple average of the first `period` closes
    - Multiplier: k = 2 / (period + 1)

    Returns an array with length `len(closes) - (period - 1)`; index 0 aligns to closes[period-1].
    """
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema_vals: List[float] = [sum(closes[:period]) / float(period)]
    for price in closes[period:]:
        ema_vals.append(price * k + ema_vals[-1] * (1.0 - k))
    return ema_vals


def ema_latest(closes: Sequence[float], period: int) -> Optional[float]:
    """Return the latest EMA value for the sequence or None if insufficient data."""
    series = ema_series(closes, period)
    return series[-1] if series else None


def macd_series(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[List[float], List[float], List[float]]:
    """Compute MACD( fast, slow, signal ).

    - MACD line = EMA_fast − EMA_slow (aligned from index slow−1)
    - Signal line = EMA(MACD, signal)
    - Histogram = MACD − Signal

    Returns triplet of equal-length lists (macd, signal_line, hist), trimmed where all defined.

    Tolerances (per REARCHITECTING): histogram diff typically ≤ 2e-4; allow ≤ 5e-4.
    """
    if slow <= 0 or fast <= 0 or signal <= 0:
        raise ValueError("MACD periods must be positive")
    if fast >= slow:
        raise ValueError("MACD requires fast < slow")
    if len(closes) < slow:
        return [], [], []

    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    # Align: ema_fast starts at index fast-1, ema_slow at slow-1
    # Shift ema_fast to align with ema_slow tail
    shift = (slow - fast)
    if len(ema_fast) <= shift:
        return [], [], []
    ema_fast_aligned = ema_fast[shift:]
    length = min(len(ema_fast_aligned), len(ema_slow))
    macd_line = [ema_fast_aligned[i] - ema_slow[i] for i in range(length)]

    sig_series = ema_series(macd_line, signal)
    if not sig_series:
        return macd_line, [], []
    # Align MACD tail to signal series
    macd_tail = macd_line[(len(macd_line) - len(sig_series)) :]
    hist = [m - s for m, s in zip(macd_tail, sig_series)]
    return macd_tail, sig_series, hist


def macd_latest(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[Tuple[float, float, float]]:
    """Return the latest (macd, signal, histogram) or None if insufficient data."""
    m, s, h = macd_series(closes, fast, slow, signal)
    if not (m and s and h):
        return None
    return m[-1], s[-1], h[-1]


def _true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], idx: int
) -> float:
    if idx == 0:
        return highs[0] - lows[0]
    prev_close = closes[idx - 1]
    return max(
        highs[idx] - lows[idx],
        abs(highs[idx] - prev_close),
        abs(lows[idx] - prev_close),
    )


def atr_wilder_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> List[float]:
    """Return Wilder ATR series.

    - TR[i] = max( high−low, |high−prev_close|, |low−prev_close| )
    - ATR[period−1] = average(TR[:period])
    - ATR[i] = (ATR[i−1]*(period−1) + TR[i]) / period

    Returns list aligned to closes from index (period−1).
    """
    n = min(len(highs), len(lows), len(closes))
    if period <= 0:
        raise ValueError("ATR period must be positive")
    if n < period:
        return []
    tr: List[float] = [_true_range(highs, lows, closes, i) for i in range(n)]
    atr_vals: List[float] = [sum(tr[:period]) / float(period)]
    for i in range(period, n):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)
    return atr_vals


def atr_wilder_latest(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> Optional[float]:
    """Return the latest Wilder ATR value or None if insufficient data."""
    series = atr_wilder_series(highs, lows, closes, period)
    return series[-1] if series else None


def utbot_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    ema_period: int = 50,
    atr_period: int = 10,
    k: float = 3.0,
) -> Dict[str, List[float]]:
    """Compute UT Bot components on closed bars.

    Baseline: EMA(closes, ema_period)
    ATR: Wilder ATR(atr_period)
    LongStop = Baseline − k × ATR
    ShortStop = Baseline + k × ATR
    Direction: +1 long when close > ShortStop (flip), −1 short when close < LongStop (flip), else hold and trail stops.

    Returns dict with lists: baseline, long_stop, short_stop, direction, buy_sell_signal
    where buy_sell_signal contains 0 (no flip), +1 (buy), −1 (sell) aligned to trailing where all values exist.

    Tolerances (per REARCHITECTING): rounding 5 decimals; absolute diff ≤ 5e−5 typical.
    """
    if ema_period <= 0 or atr_period <= 0:
        raise ValueError("UT Bot periods must be positive")
    n = len(closes)
    if n == 0 or len(highs) < n or len(lows) < n:
        return {
            "baseline": [],
            "long_stop": [],
            "short_stop": [],
            "direction": [],
            "buy_sell_signal": [],
        }

    base = ema_series(closes, ema_period)
    atr = atr_wilder_series(highs, lows, closes, atr_period)
    # Align baseline and ATR
    # baseline starts at idx ema_period-1; atr at atr_period-1
    start_shift = max(0, (atr_period - 1) - (ema_period - 1))
    if len(base) <= start_shift:
        return {
            "baseline": [],
            "long_stop": [],
            "short_stop": [],
            "direction": [],
            "buy_sell_signal": [],
        }
    base_aligned = base[start_shift:]
    length = min(len(base_aligned), len(atr))
    base_aligned = base_aligned[:length]
    atr_aligned = atr[:length]

    long_stop: List[float] = []
    short_stop: List[float] = []
    direction: List[int] = []
    flips: List[int] = []

    # The closes alignment to base_aligned: closes index starts at idx = max(ema_period, atr_period) - 1
    price_start_idx = max(ema_period, atr_period) - 1 + start_shift
    prices = list(closes[price_start_idx : price_start_idx + length])

    prev_long = 0.0
    prev_short = 0.0
    curr_dir = 0

    for i in range(length):
        b = base_aligned[i]
        a = atr_aligned[i]
        l_stop = b - k * a
        s_stop = b + k * a

        # Initialize with neutral trailing
        if i == 0:
            prev_long = l_stop
            prev_short = s_stop
            price = prices[i]
            if price >= s_stop:
                curr_dir = +1
            elif price <= l_stop:
                curr_dir = -1
            else:
                curr_dir = 0
            direction.append(curr_dir)
            long_stop.append(round(l_stop, 5))
            short_stop.append(round(s_stop, 5))
            flips.append(0)
            continue

        price = prices[i]
        # Trail stops depending on current direction
        if curr_dir >= 0:
            # Long mode: long stop can only move up
            l_stop = max(l_stop, prev_long)
            if price < l_stop:
                # flip to short
                curr_dir = -1
                flips.append(-1)
            else:
                flips.append(0)
        if curr_dir <= 0:
            # Short mode: short stop can only move down
            s_stop = min(s_stop, prev_short)
            if curr_dir == -1 and price > s_stop:
                # flip to long
                curr_dir = +1
                flips[-1] = +1 if flips[-1] == 0 else flips[-1]
        direction.append(curr_dir)
        prev_long = l_stop
        prev_short = s_stop
        long_stop.append(round(l_stop, 5))
        short_stop.append(round(s_stop, 5))

    return {
        "baseline": [round(x, 5) for x in base_aligned],
        "long_stop": long_stop,
        "short_stop": short_stop,
        "direction": direction,
        "buy_sell_signal": flips,
    }


def utbot_latest(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    ema_period: int = 50,
    atr_period: int = 10,
    k: float = 3.0,
) -> Optional[Tuple[float, float, int, int]]:
    """Return latest (baseline, stop, direction, flip_signal) aligned to last closed bar.

    stop is the active trailing stop depending on direction.
    """
    res = utbot_series(highs, lows, closes, ema_period, atr_period, k)
    base = res["baseline"]
    l = res["long_stop"]
    s = res["short_stop"]
    d = res["direction"]
    f = res["buy_sell_signal"]
    if not (base and l and s and d and f):
        return None
    idx = -1
    active_stop = l[idx] if d[idx] >= 0 else s[idx]
    return base[idx], active_stop, d[idx], f[idx]


def ichimoku_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> Dict[str, List[float]]:
    """Compute Ichimoku components.

    - Tenkan (Conversion): (HH9 + LL9) / 2
    - Kijun (Base): (HH26 + LL26) / 2
    - Senkou A: (Tenkan + Kijun) / 2 shifted forward by `displacement`
    - Senkou B: (HH52 + LL52) / 2 shifted forward by `displacement`
    - Chikou: close shifted backward by `displacement`

    For historical parity, arrays are left-aligned where each component is defined; forward/back shifts are represented by truncation (no future padding).

    Tolerances: deterministic on bid OHLC; expect equality; minor ≤ 1 pip variance possible versus alt price bases.
    """
    n = min(len(highs), len(lows), len(closes))
    if n == 0:
        return {"tenkan": [], "kijun": [], "senkou_a": [], "senkou_b": [], "chikou": []}

    def midpoint(hh: float, ll: float) -> float:
        return (hh + ll) / 2.0

    tenkan: List[float] = []
    kijun: List[float] = []
    for i in range(n):
        if i + 1 >= tenkan_period:
            window_h = max(highs[i + 1 - tenkan_period : i + 1])
            window_l = min(lows[i + 1 - tenkan_period : i + 1])
            tenkan.append(midpoint(window_h, window_l))
        if i + 1 >= kijun_period:
            window_h = max(highs[i + 1 - kijun_period : i + 1])
            window_l = min(lows[i + 1 - kijun_period : i + 1])
            kijun.append(midpoint(window_h, window_l))

    # Align tenkan and kijun (tenkan starts at idx tenkan_period-1; kijun at kijun_period-1)
    if not kijun:
        return {"tenkan": [], "kijun": [], "senkou_a": [], "senkou_b": [], "chikou": []}
    tenkan_shift = (kijun_period - tenkan_period)
    tenkan_aligned = tenkan[tenkan_shift:] if tenkan_shift > 0 else tenkan
    length = min(len(tenkan_aligned), len(kijun))
    tenkan_aligned = tenkan_aligned[:length]
    kijun_aligned = kijun[:length]

    senkou_a = [(tenkan_aligned[i] + kijun_aligned[i]) / 2.0 for i in range(length)]

    # Senkou B aligned to kijun start
    senkou_b_raw: List[float] = []
    for i in range(n):
        if i + 1 >= senkou_b_period:
            window_h = max(highs[i + 1 - senkou_b_period : i + 1])
            window_l = min(lows[i + 1 - senkou_b_period : i + 1])
            senkou_b_raw.append(midpoint(window_h, window_l))
    # Align Senkou B to kijun start
    if not senkou_b_raw:
        return {"tenkan": [], "kijun": [], "senkou_a": [], "senkou_b": [], "chikou": []}
    # Align senkou_b to kijun alignment: shift = kijun_period - senkou_b_period
    sb_shift = (kijun_period - senkou_b_period)
    senkou_b_aligned = senkou_b_raw[sb_shift:] if sb_shift > 0 else senkou_b_raw
    sb_length = min(len(kijun_aligned), len(senkou_b_aligned))
    senkou_b_aligned = senkou_b_aligned[:sb_length]
    # Re-trim tenkan/kijun/senkou_a to sb_length
    tenkan_aligned = tenkan_aligned[:sb_length]
    kijun_aligned = kijun_aligned[:sb_length]
    senkou_a = senkou_a[:sb_length]

    # Chikou span: close shifted back by displacement relative to aligned arrays
    # Determine price start index for aligned arrays relative to original series
    aligned_start = kijun_period - 1
    chikou_series: List[float] = []
    # For each aligned index j, corresponding source price index is aligned_start + j
    for j in range(sb_length):
        src_idx = aligned_start + j
        if src_idx - displacement >= 0:
            chikou_series.append(closes[src_idx - displacement])
    # To keep equal lengths, trim aligned arrays to match chikou length
    if chikou_series:
        trim = sb_length - len(chikou_series)
        if trim > 0:
            tenkan_aligned = tenkan_aligned[trim:]
            kijun_aligned = kijun_aligned[trim:]
            senkou_a = senkou_a[trim:]
            senkou_b_aligned = senkou_b_aligned[trim:]

    return {
        "tenkan": tenkan_aligned,
        "kijun": kijun_aligned,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b_aligned,
        "chikou": chikou_series,
    }


def ichimoku_latest(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> Optional[Dict[str, float]]:
    """Return latest Ichimoku values as a dict or None if insufficient data."""
    series = ichimoku_series(
        highs, lows, closes, tenkan_period, kijun_period, senkou_b_period, displacement
    )
    if not series["tenkan"]:
        return None
    idx = -1
    return {
        "tenkan": series["tenkan"][idx],
        "kijun": series["kijun"][idx],
        "senkou_a": series["senkou_a"][idx],
        "senkou_b": series["senkou_b"][idx],
        "chikou": series["chikou"][idx],
    }


def rsi_series(closes: Sequence[float], period: int = 14) -> List[float]:
    """Alias of Wilder RSI series for centralization."""
    return rsi_series_wilder(closes, period)


def rsi_latest(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Alias of Wilder latest RSI value for centralization."""
    return rsi_latest_wilder(closes, period)


__all__ = [
    # RSI
    "rsi_series",
    "rsi_latest",
    "closed_only_closes",
    # EMA
    "ema_series",
    "ema_latest",
    # MACD
    "macd_series",
    "macd_latest",
    # ATR / UT Bot
    "atr_wilder_series",
    "atr_wilder_latest",
    "utbot_series",
    "utbot_latest",
    # Ichimoku
    "ichimoku_series",
    "ichimoku_latest",
]



