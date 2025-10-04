from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Core helpers and models
from .models import Timeframe as TF
from .mt5_utils import get_ohlc_data
from .indicator_cache import indicator_cache
from .indicators import (
    rsi_series as ind_rsi_series,
    atr_wilder_series as ind_atr_wilder_series,
    utbot_series as ind_utbot_series,
    ichimoku_series as ind_ichimoku_series,
)


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[k])


def _clamp(x: float, lo: float, hi: float) -> float:
    return hi if x > hi else (lo if x < lo else x)


async def compute_quantum_for_symbol(symbol: str) -> Dict[str, Any]:
    """Compute Quantum Analysis (heatmap) per-timeframe and overall Buy/Sell% for a symbol.

    Returns a dict with keys:
      - per_timeframe: { tf: { buy_percent, sell_percent, final_score } }
      - overall: { scalper: {...}, swingtrader: {...} }
      - bar_times: { tf: last_closed_bar_time_ms }

    Parity: Mirrors HeatmapTrackerAlertService scoring rules (K=3, quiet-market damping,
    equal indicator weights, clamp, Final/Buy%/Sell% formulas) and style weights.
    """
    # Style timeframe weights
    tf_weights_map: Dict[str, Dict[str, float]] = {
        "scalper": {"5M": 0.30, "15M": 0.30, "30M": 0.20, "1H": 0.15, "4H": 0.05, "1D": 0.0},
        "swingtrader": {"30M": 0.10, "1H": 0.25, "4H": 0.35, "1D": 0.30},
    }

    # Baseline timeframes to compute per-timeframe values (1M..1D as requested)
    baseline_tfs: List[str] = ["1M", "5M", "15M", "30M", "1H", "4H", "1D"]
    tf_map: Dict[str, TF] = {
        "1M": TF.M1,
        "5M": TF.M5,
        "15M": TF.M15,
        "30M": TF.M30,
        "1H": TF.H1,
        "4H": TF.H4,
        "1D": TF.D1,
        "1W": TF.W1,
    }

    indicators: List[str] = ["EMA21", "EMA50", "EMA200", "MACD", "RSI", "UTBOT", "ICHIMOKU"]
    ind_weight: float = 1.0 / float(len(indicators))
    K: int = 3

    per_timeframe: Dict[str, Dict[str, float]] = {}
    bar_times: Dict[str, Optional[int]] = {}

    # Helpers for per-indicator scoring rules
    def score_cell(signal: str, is_new: bool, ind_name: str, is_quiet: bool) -> float:
        base = 1.0 if signal == "buy" else (-1.0 if signal == "sell" else 0.0)
        if base == 0.0:
            return 0.0
        if is_new:
            base = base + (0.25 if base > 0 else -0.25)
        if is_quiet and ind_name in ("MACD", "UTBOT"):
            base *= 0.5
        return _clamp(base, -1.25, 1.25)

    for tf_code in baseline_tfs:
        mtf = tf_map.get(tf_code)
        if not mtf:
            continue
        try:
            bars = get_ohlc_data(symbol, mtf, 300)
            if not bars:
                continue
            closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
            if len(closed_bars) < 60:
                continue
            closes = [float(b.close) for b in closed_bars]
            highs = [float(b.high) for b in closed_bars]
            lows = [float(b.low) for b in closed_bars]
            ts_list = [int(b.time) for b in closed_bars]

            # Quiet market detection using ATR10 5th percentile over last 200
            atrs = ind_atr_wilder_series(highs, lows, closes, 10)
            is_quiet = False
            if len(atrs) >= 200:
                last_atr = float(atrs[-1])
                p5 = _percentile([float(x) for x in atrs[-200:]], 5.0)
                is_quiet = last_atr < p5

            # RSI(14) from cache with fallback from series
            rsi_recent = await indicator_cache.get_recent_rsi(symbol, tf_code, 14, K + 2)
            if not rsi_recent or len(rsi_recent) < 2:
                try:
                    rsis = ind_rsi_series(closes, 14)
                    if rsis:
                        rsi_recent = [(ts_list[-len(rsis) + i], float(rsis[i])) for i in range(len(rsis))][- (K + 2):]
                except Exception:
                    rsi_recent = None

            def rsi_signal_from_recent() -> Tuple[str, bool]:
                if not rsi_recent or len(rsi_recent) < 2:
                    return "neutral", False
                r = float(rsi_recent[-1][1])
                sig = "buy" if r <= 30.0 else ("sell" if r >= 70.0 else "neutral")
                is_new = False
                window = [float(v) for _, v in rsi_recent[- (K + 1):]]
                for i in range(1, len(window)):
                    prev, curr = window[i - 1], window[i]
                    if (prev < 50.0 <= curr) or (prev > 50.0 >= curr):
                        is_new = True
                        break
                    if (prev > 70.0 and curr <= 70.0) or (prev < 30.0 and curr >= 30.0) or (prev <= 70.0 and curr > 70.0) or (prev >= 30.0 and curr < 30.0):
                        is_new = True
                        break
                return sig, is_new

            # EMA(21/50/200) from cache aligned by timestamps
            ema_recent_21 = await indicator_cache.get_recent_ema(symbol, tf_code, 21, K + 3)
            ema_recent_50 = await indicator_cache.get_recent_ema(symbol, tf_code, 50, K + 3)
            ema_recent_200 = await indicator_cache.get_recent_ema(symbol, tf_code, 200, K + 3)

            ts_to_close: Dict[int, float] = {int(b.time): float(b.close) for b in closed_bars}

            def ema_signal_from_recent(ema_recent: Optional[List[Tuple[int, float]]]) -> Tuple[str, bool]:
                if not ema_recent or len(ema_recent) < 2:
                    return "neutral", False
                aligned: List[Tuple[int, float, float]] = []  # (ts, close, ema)
                for ts, ev in ema_recent:
                    c = ts_to_close.get(int(ts))
                    if c is not None:
                        aligned.append((int(ts), float(c), float(ev)))
                if len(aligned) < 2:
                    return "neutral", False
                _, c_prev, e_prev = aligned[-2]
                _, c_curr, e_curr = aligned[-1]
                sig = "buy" if c_curr > e_curr else ("sell" if c_curr < e_curr else "neutral")
                is_new = False
                for i in range(1, min(K, len(aligned) - 1) + 1):
                    _, cp, ep = aligned[-(i + 1)]
                    _, cc, ec = aligned[-i]
                    if (cp <= ep and cc > ec) or (cp >= ep and cc < ec):
                        is_new = True
                        break
                return sig, is_new

            # MACD from cache (12,26,9)
            macd_recent = await indicator_cache.get_recent_macd(symbol, tf_code, 12, 26, 9, K + 3)

            def macd_signal_from_recent() -> Tuple[str, bool]:
                if not macd_recent or len(macd_recent) < 1:
                    return "neutral", False
                _, m, s, _h = macd_recent[-1]
                sig = "buy" if (m > s and m > 0) else ("sell" if (m < s and m < 0) else "neutral")
                is_new = False
                for i in range(1, min(K, len(macd_recent) - 1) + 1):
                    _, m_prev, s_prev, _ = macd_recent[-(i + 1)]
                    _, m_curr, s_curr, _ = macd_recent[-i]
                    if (m_prev <= s_prev and m_curr > s_curr) or (m_prev >= s_prev and m_curr < s_curr):
                        is_new = True
                        break
                return sig, is_new

            # UTBot via centralized helper
            def utbot_signal() -> Tuple[str, bool]:
                res = ind_utbot_series(highs, lows, closes, 50, 10, 3.0)
                base = res.get("baseline") or []
                l = res.get("long_stop") or []
                s = res.get("short_stop") or []
                flips = res.get("buy_sell_signal") or []
                if not (base and l and s):
                    return "neutral", False
                price = closes[-1]
                pos = "buy" if price > s[-1] else ("sell" if price < l[-1] else "neutral")
                is_new = any(v != 0 for v in flips[-K:]) if flips else False
                return pos, is_new

            # Ichimoku via centralized helper
            def ichimoku_signal() -> Tuple[str, bool]:
                series = ind_ichimoku_series(highs, lows, closes, 9, 26, 52, 26)
                tenkan = series.get("tenkan") or []
                kijun = series.get("kijun") or []
                sa = series.get("senkou_a") or []
                sb = series.get("senkou_b") or []
                if not (tenkan and kijun and sa and sb):
                    return "neutral", False
                up_cloud = max(sa[-1], sb[-1])
                dn_cloud = min(sa[-1], sb[-1])
                price = closes[-1]
                if price > up_cloud:
                    sig = "buy"
                elif price < dn_cloud:
                    sig = "sell"
                else:
                    sig = "neutral"
                    for i in range(1, min(K, len(tenkan) - 1, len(kijun) - 1) + 1):
                        t_prev, k_prev = tenkan[-(i + 1)], kijun[-(i + 1)]
                        t_curr, k_curr = tenkan[-i], kijun[-i]
                        if t_prev <= k_prev and t_curr > k_curr:
                            sig = "buy"
                            break
                        if t_prev >= k_prev and t_curr < k_curr:
                            sig = "sell"
                            break
                    if sig == "neutral":
                        if sa[-1] > sb[-1]:
                            sig = "buy"
                        elif sa[-1] < sb[-1]:
                            sig = "sell"
                # New if TK cross or cloud breakout in last K
                is_new = False
                for i in range(1, min(K, len(tenkan) - 1, len(kijun) - 1) + 1):
                    t_prev, k_prev = tenkan[-(i + 1)], kijun[-(i + 1)]
                    t_curr, k_curr = tenkan[-i], kijun[-i]
                    if (t_prev <= k_prev and t_curr > k_curr) or (t_prev >= k_prev and t_curr < k_curr):
                        is_new = True
                        break
                if not is_new:
                    for i in range(1, min(K, len(sa), len(sb), len(closes)) + 1):
                        up_c = max(sa[-i], sb[-i])
                        dn_c = min(sa[-i], sb[-i])
                        pr = closes[-i]
                        if pr > up_c or pr < dn_c:
                            is_new = True
                            break
                return sig, is_new

            # Compute per-indicator signals (and newness) once for this timeframe
            ema21_sig, ema21_new = ema_signal_from_recent(ema_recent_21)
            ema50_sig, ema50_new = ema_signal_from_recent(ema_recent_50)
            ema200_sig, ema200_new = ema_signal_from_recent(ema_recent_200)
            macd_sig, macd_new = macd_signal_from_recent()
            rsi_sig, rsi_new = rsi_signal_from_recent()
            utbot_sig, utbot_new = utbot_signal()
            ichi_sig, ichi_new = ichimoku_signal()

            # Aggregate per-timeframe
            per_tf_sum = 0.0
            per_tf_sum += score_cell(ema21_sig, ema21_new, "EMA21", is_quiet) * ind_weight
            per_tf_sum += score_cell(ema50_sig, ema50_new, "EMA50", is_quiet) * ind_weight
            per_tf_sum += score_cell(ema200_sig, ema200_new, "EMA200", is_quiet) * ind_weight
            per_tf_sum += score_cell(macd_sig, macd_new, "MACD", is_quiet) * ind_weight
            per_tf_sum += score_cell(rsi_sig, rsi_new, "RSI", is_quiet) * ind_weight
            per_tf_sum += score_cell(utbot_sig, utbot_new, "UTBOT", is_quiet) * ind_weight
            per_tf_sum += score_cell(ichi_sig, ichi_new, "ICHIMOKU", is_quiet) * ind_weight

            final = 100.0 * (per_tf_sum / 1.25)
            final = _clamp(final, -100.0, 100.0)
            buy_pct = (final + 100.0) / 2.0
            sell_pct = 100.0 - buy_pct

            per_timeframe[tf_code] = {
                "buy_percent": float(buy_pct),
                "sell_percent": float(sell_pct),
                "final_score": float(final),
                "indicators": {
                    "EMA21": {"signal": ema21_sig, "is_new": bool(ema21_new)},
                    "EMA50": {"signal": ema50_sig, "is_new": bool(ema50_new)},
                    "EMA200": {"signal": ema200_sig, "is_new": bool(ema200_new)},
                    "MACD": {"signal": macd_sig, "is_new": bool(macd_new)},
                    "RSI": {"signal": rsi_sig, "is_new": bool(rsi_new)},
                    "UTBOT": {"signal": utbot_sig, "is_new": bool(utbot_new)},
                    "ICHIMOKU": {"signal": ichi_sig, "is_new": bool(ichi_new)},
                },
            }
            bar_times[tf_code] = int(ts_list[-1]) if ts_list else None
        except Exception:
            # Skip timeframe on failure
            continue

    # Overall aggregation by style
    overall: Dict[str, Dict[str, float]] = {}
    for style, weights in tf_weights_map.items():
        raw = 0.0
        for tf_code, w_tf in weights.items():
            if w_tf <= 0:
                continue
            tf_vals = per_timeframe.get(tf_code)
            if not tf_vals:
                continue
            # Recover per-tf raw from final: final = 100 * (raw_tf / 1.25)
            # So raw_tf = final * 1.25 / 100
            tf_final = float(tf_vals.get("final_score", 0.0))
            tf_raw = (tf_final * 1.25) / 100.0
            raw += tf_raw * w_tf
        final = 100.0 * (raw / 1.25)
        final = _clamp(final, -100.0, 100.0)
        buy_pct = (final + 100.0) / 2.0
        sell_pct = 100.0 - buy_pct
        overall[style] = {
            "buy_percent": float(buy_pct),
            "sell_percent": float(sell_pct),
            "final_score": float(final),
        }

    return {
        "per_timeframe": per_timeframe,
        "overall": overall,
        "bar_times": bar_times,
    }


