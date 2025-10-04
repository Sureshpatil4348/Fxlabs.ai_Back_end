from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple


def _init_mt5() -> Optional["mt5"]:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception:
        print("[SKIP] MetaTrader5 module not available. Skipping indicator unit checks.")
        return None
    try:
        kwargs: Dict[str, str] = {}
        mt5_path = os.environ.get("MT5_TERMINAL_PATH")
        if mt5_path:
            kwargs["path"] = mt5_path
        if not mt5.initialize(**kwargs):
            err = mt5.last_error() if hasattr(mt5, "last_error") else ("unknown", "error")
            print(f"[SKIP] MT5 initialize failed: {err}. Skipping indicator unit checks.")
            return None
        return mt5
    except Exception as e:
        print(f"[SKIP] MT5 init error: {e}. Skipping indicator unit checks.")
        return None


def _shutdown_mt5(mt5) -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def _ema_series_ref(closes: Sequence[float], period: int) -> List[float]:
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema_vals: List[float] = [sum(closes[:period]) / float(period)]
    for price in closes[period:]:
        ema_vals.append(price * k + ema_vals[-1] * (1.0 - k))
    return ema_vals


def _macd_series_ref(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[List[float], List[float], List[float]]:
    if slow <= 0 or fast <= 0 or signal <= 0:
        raise ValueError("MACD periods must be positive")
    if fast >= slow:
        raise ValueError("MACD requires fast < slow")
    if len(closes) < slow:
        return [], [], []
    ema_fast = _ema_series_ref(closes, fast)
    ema_slow = _ema_series_ref(closes, slow)
    shift = (slow - fast)
    if len(ema_fast) <= shift:
        return [], [], []
    ema_fast_aligned = ema_fast[shift:]
    length = min(len(ema_fast_aligned), len(ema_slow))
    macd_line = [ema_fast_aligned[i] - ema_slow[i] for i in range(length)]
    sig_series = _ema_series_ref(macd_line, signal)
    if not sig_series:
        return macd_line, [], []
    macd_tail = macd_line[(len(macd_line) - len(sig_series)) :]
    hist = [m - s for m, s in zip(macd_tail, sig_series)]
    return macd_tail, sig_series, hist


def run_unit_checks() -> int:
    """Run indicator unit checks over a small set of symbols×timeframes.

    Returns process exit code (0=OK, 1=FAIL, 0 when skipped due to MT5 unavailability).
    """
    mt5 = _init_mt5()
    if mt5 is None:
        return 0

    try:
        # Local imports after MT5 is initialized
        from app.models import Timeframe
        from app.constants import RSI_SUPPORTED_SYMBOLS
        from app.mt5_utils import ensure_symbol_selected, get_ohlc_data
        import app.indicators as ind
        from app.rsi_utils import calculate_rsi_series as rsi_series_wilder

        # Tolerances per REARCHITECTING.md
        RSI_MAX_ABS_DIFF = 0.15
        MACD_HIST_MAX_ABS_DIFF = 5e-4

        # Select 3–5 symbols that are likely to exist; filter by availability
        preferred = [
            "EURUSDm",
            "GBPUSDm",
            "USDJPYm",
            "XAUUSDm",
            "BTCUSDm",
        ]
        symbols: List[str] = []
        for s in preferred:
            try:
                ensure_symbol_selected(s)
                symbols.append(s)
            except Exception:
                continue
            if len(symbols) >= 5:
                break
        if not symbols:
            # Fall back to first 3 supported if ensure/select failed silently
            symbols = RSI_SUPPORTED_SYMBOLS[:3]

        timeframes = [Timeframe.M5, Timeframe.H1, Timeframe.D1]

        total_cases = 0
        passed = 0
        failed_messages: List[str] = []

        for sym in symbols:
            for tf in timeframes:
                try:
                    bars = get_ohlc_data(sym, tf, 300)
                except Exception as e:
                    print(f"[WARN] Skipping {sym} {tf.value}: fetch error: {e}")
                    continue

                closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
                if len(closed_bars) < 60:
                    print(f"[WARN] Skipping {sym} {tf.value}: insufficient closed bars ({len(closed_bars)})")
                    continue

                closes = [b.close for b in closed_bars]
                highs = [b.high for b in closed_bars]
                lows = [b.low for b in closed_bars]

                # 1) RSI parity (ind.rsi_series vs rsi_utils)
                total_cases += 1
                try:
                    rs_ind = ind.rsi_series(closes, 14)
                    rs_ref = rsi_series_wilder(closes, 14)
                    if rs_ind and rs_ref:
                        n = min(len(rs_ind), len(rs_ref))
                        diffs = [abs(rs_ind[-i] - rs_ref[-i]) for i in range(1, n + 1)]
                        max_diff = max(diffs) if diffs else 0.0
                        assert max_diff <= RSI_MAX_ABS_DIFF, f"RSI diff {max_diff:.4f} > {RSI_MAX_ABS_DIFF}"
                    passed += 1
                except Exception as e:
                    failed_messages.append(f"RSI parity failed {sym} {tf.value}: {e}")

                # 2) EMA parity (ind.ema_series vs reference)
                for period in (21, 50, 200):
                    total_cases += 1
                    try:
                        es = ind.ema_series(closes, period)
                        er = _ema_series_ref(closes, period)
                        assert len(es) == len(er) and len(es) > 0, "EMA length mismatch or empty"
                        # Use tight tolerance since ref is identical math
                        diffs = [abs(a - b) for a, b in zip(es[-100:], er[-100:])]
                        max_diff = max(diffs) if diffs else 0.0
                        assert max_diff <= 1e-9, f"EMA{period} diff {max_diff:.2e} > 1e-9"
                        passed += 1
                    except Exception as e:
                        failed_messages.append(f"EMA{period} parity failed {sym} {tf.value}: {e}")

                # 3) MACD parity (ind.macd_series vs reference)
                total_cases += 1
                try:
                    m, s, h = ind.macd_series(closes, 12, 26, 9)
                    mr, sr, hr = _macd_series_ref(closes, 12, 26, 9)
                    assert len(h) == len(hr) and len(h) > 0, "MACD length mismatch or empty"
                    diffs = [abs(a - b) for a, b in zip(h[-100:], hr[-100:])]
                    max_diff = max(diffs) if diffs else 0.0
                    assert max_diff <= MACD_HIST_MAX_ABS_DIFF, (
                        f"MACD hist diff {max_diff:.6f} > {MACD_HIST_MAX_ABS_DIFF}"
                    )
                    passed += 1
                except Exception as e:
                    failed_messages.append(f"MACD parity failed {sym} {tf.value}: {e}")

                # 4) UTBot & Ichimoku sanity checks
                total_cases += 1
                try:
                    ut = ind.utbot_series(highs, lows, closes, ema_period=50, atr_period=10, k=3.0)
                    keys = ["baseline", "long_stop", "short_stop", "direction", "buy_sell_signal"]
                    assert all(k in ut for k in keys), "UTBot missing keys"
                    ln = min(len(ut["baseline"]), len(ut["long_stop"]))
                    ln = min(ln, len(ut["short_stop"]))
                    ln = min(ln, len(ut["direction"]))
                    ln = min(ln, len(ut["buy_sell_signal"]))
                    assert ln > 0, "UTBot produced empty series"
                    assert all(v in (-1, 0, 1) for v in ut["direction"][-min(ln, 100):]), "UTBot direction values invalid"
                    passed += 1
                except Exception as e:
                    failed_messages.append(f"UTBot sanity failed {sym} {tf.value}: {e}")

                total_cases += 1
                try:
                    ich = ind.ichimoku_series(highs, lows, closes, 9, 26, 52, 26)
                    for k in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou"):
                        assert k in ich, f"Ichimoku missing {k}"
                    lens = [len(ich[k]) for k in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou")]
                    assert min(lens) > 0, "Ichimoku produced empty series"
                    # Equalize non-zero lengths for aligned components
                    assert len(ich["tenkan"]) == len(ich["kijun"]) == len(ich["senkou_a"]) == len(ich["senkou_b"]), (
                        "Ichimoku aligned components length mismatch"
                    )
                    passed += 1
                except Exception as e:
                    failed_messages.append(f"Ichimoku sanity failed {sym} {tf.value}: {e}")

        print(
            f"[RESULT] Indicator unit checks: passed={passed}/{total_cases} failures={len(failed_messages)}"
        )
        for msg in failed_messages:
            print(f"[FAIL] {msg}")

        return 0 if not failed_messages else 1
    finally:
        _shutdown_mt5(mt5)


if __name__ == "__main__":
    exit_code = run_unit_checks()
    sys.exit(exit_code)


