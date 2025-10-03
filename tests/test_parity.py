from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple


def _init_mt5() -> Optional["mt5"]:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception:
        print("[SKIP] MetaTrader5 module not available. Skipping parity checks.")
        return None
    try:
        kwargs: Dict[str, str] = {}
        mt5_path = os.environ.get("MT5_TERMINAL_PATH")
        if mt5_path:
            kwargs["path"] = mt5_path
        if not mt5.initialize(**kwargs):
            err = mt5.last_error() if hasattr(mt5, "last_error") else ("unknown", "error")
            print(f"[SKIP] MT5 initialize failed: {err}. Skipping parity checks.")
            return None
        return mt5
    except Exception as e:
        print(f"[SKIP] MT5 init error: {e}. Skipping parity checks.")
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


def _is_today_utc(ts_ms: int) -> bool:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.date() == datetime.now(timezone.utc).date()


def run_parity_checks() -> int:
    """Compare indicator results across the last N closed bars within tolerances.

    Tolerances per REARCHITECTING.md:
    - RSI (Wilder): abs diff ≤ 0.15
    - EMA(21/50/200): abs diff ≈ 0 (identical math), assert ≤ 1e-9 on tail
    - MACD(12,26,9) histogram: abs diff ≤ 5e-4
    - Daily % change (Bid): sanity parity with manual recompute within ≤ 0.10%
    """
    mt5 = _init_mt5()
    if mt5 is None:
        return 0

    try:
        from app.models import Timeframe
        from app.constants import RSI_SUPPORTED_SYMBOLS
        from app.mt5_utils import ensure_symbol_selected, get_ohlc_data, get_current_tick, get_daily_change_pct_bid
        import app.indicators as ind
        from app.rsi_utils import calculate_rsi_series as rsi_series_wilder

        RSI_MAX_ABS_DIFF = 0.15
        MACD_HIST_MAX_ABS_DIFF = 5e-4
        DAILY_CHANGE_MAX_ABS_DIFF_PCT = 0.10  # percent points (≤ 10 bps)

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
            symbols = RSI_SUPPORTED_SYMBOLS[:3]

        timeframes = [Timeframe.M5, Timeframe.H1, Timeframe.D1]

        total_cases = 0
        passed = 0
        failures: List[str] = []

        for sym in symbols:
            # Daily % change parity (Bid basis)
            total_cases += 1
            try:
                tick = get_current_tick(sym)
                if tick and tick.bid is not None:
                    d1_bars = get_ohlc_data(sym, Timeframe.D1, 2)
                    if d1_bars:
                        latest = d1_bars[-1]
                        prev = d1_bars[-2] if len(d1_bars) > 1 else None
                        if _is_today_utc(latest.time):
                            ref = latest.closeBid if latest.closeBid is not None else latest.close
                            # Spec prefers D1 open for today; if openBid exists, use it over closeBid for today
                            ref = latest.openBid if latest.openBid is not None else latest.open if latest.open is not None else ref
                        else:
                            ref = prev.closeBid if prev and prev.closeBid is not None else (prev.close if prev else None)
                        if ref and ref != 0:
                            manual = 100.0 * (float(tick.bid) - float(ref)) / float(ref)
                            func_val = get_daily_change_pct_bid(sym)
                            if func_val is not None:
                                diff_pct = abs(manual - func_val)
                                assert diff_pct <= DAILY_CHANGE_MAX_ABS_DIFF_PCT, (
                                    f"daily_change_pct diff {diff_pct:.4f}% > {DAILY_CHANGE_MAX_ABS_DIFF_PCT}%"
                                )
                passed += 1
            except Exception as e:
                failures.append(f"Daily% parity failed {sym}: {e}")

            for tf in timeframes:
                try:
                    bars = get_ohlc_data(sym, tf, 300)
                except Exception as e:
                    print(f"[WARN] Skipping {sym} {tf.value}: fetch error: {e}")
                    continue

                closed = [b for b in bars if getattr(b, "is_closed", None) is not False]
                if len(closed) < 220:
                    print(f"[WARN] Skipping {sym} {tf.value}: insufficient closed bars ({len(closed)})")
                    continue

                closes = [b.close for b in closed]

                # RSI parity over tail N bars
                total_cases += 1
                try:
                    rs_a = ind.rsi_series(closes, 14)
                    rs_b = rsi_series_wilder(closes, 14)
                    if rs_a and rs_b:
                        n = min(len(rs_a), len(rs_b), 150)
                        diffs = [abs(rs_a[-i] - rs_b[-i]) for i in range(1, n + 1)]
                        max_diff = max(diffs) if diffs else 0.0
                        assert max_diff <= RSI_MAX_ABS_DIFF, f"RSI diff {max_diff:.4f} > {RSI_MAX_ABS_DIFF}"
                    passed += 1
                except Exception as e:
                    failures.append(f"RSI parity failed {sym} {tf.value}: {e}")

                # EMA parity (21/50/200) — exact math
                for period in (21, 50, 200):
                    total_cases += 1
                    try:
                        e_a = ind.ema_series(closes, period)
                        e_b = _ema_series_ref(closes, period)
                        assert len(e_a) == len(e_b) and len(e_a) > 0, "EMA length mismatch or empty"
                        m = min(len(e_a), 150)
                        diffs = [abs(e_a[-i] - e_b[-i]) for i in range(1, m + 1)]
                        max_diff = max(diffs) if diffs else 0.0
                        assert max_diff <= 1e-9, f"EMA{period} diff {max_diff:.2e} > 1e-9"
                        passed += 1
                    except Exception as e:
                        failures.append(f"EMA{period} parity failed {sym} {tf.value}: {e}")

                # MACD histogram parity
                total_cases += 1
                try:
                    _, _, h_a = ind.macd_series(closes, 12, 26, 9)
                    _, _, h_b = _macd_series_ref(closes, 12, 26, 9)
                    assert len(h_a) == len(h_b) and len(h_a) > 0, "MACD length mismatch or empty"
                    m = min(len(h_a), 150)
                    diffs = [abs(h_a[-i] - h_b[-i]) for i in range(1, m + 1)]
                    max_diff = max(diffs) if diffs else 0.0
                    assert max_diff <= MACD_HIST_MAX_ABS_DIFF, (
                        f"MACD hist diff {max_diff:.6f} > {MACD_HIST_MAX_ABS_DIFF}"
                    )
                    passed += 1
                except Exception as e:
                    failures.append(f"MACD parity failed {sym} {tf.value}: {e}")

        print(f"[RESULT] Parity checks: passed={passed}/{total_cases} failures={len(failures)}")
        for msg in failures:
            print(f"[FAIL] {msg}")
        return 0 if not failures else 1
    finally:
        _shutdown_mt5(mt5)


if __name__ == "__main__":
    code = run_parity_checks()
    sys.exit(code)


