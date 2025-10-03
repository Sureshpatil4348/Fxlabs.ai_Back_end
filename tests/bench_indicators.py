from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Tuple


def _init_mt5() -> Optional["mt5"]:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception:
        print("[SKIP] MetaTrader5 module not available. Skipping micro-benchmark.")
        return None
    try:
        kwargs: Dict[str, str] = {}
        mt5_path = os.environ.get("MT5_TERMINAL_PATH")
        if mt5_path:
            kwargs["path"] = mt5_path
        if not mt5.initialize(**kwargs):
            err = mt5.last_error() if hasattr(mt5, "last_error") else ("unknown", "error")
            print(f"[SKIP] MT5 initialize failed: {err}. Skipping micro-benchmark.")
            return None
        return mt5
    except Exception as e:
        print(f"[SKIP] MT5 init error: {e}. Skipping micro-benchmark.")
        return None


def _shutdown_mt5(mt5) -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def format_ms(ms: float) -> str:
    return f"{ms:.2f} ms"


def main() -> int:
    mt5 = _init_mt5()
    if mt5 is None:
        return 0

    try:
        from app.models import Timeframe
        from app.mt5_utils import ensure_symbol_selected, get_ohlc_data
        import app.indicators as ind

        symbols = []
        for s in ("EURUSDm", "GBPUSDm", "USDJPYm", "XAUUSDm", "BTCUSDm"):
            try:
                ensure_symbol_selected(s)
                symbols.append(s)
            except Exception:
                continue
        if not symbols:
            symbols = ["EURUSDm", "GBPUSDm", "USDJPYm"]

        timeframes = [Timeframe.M5, Timeframe.H1, Timeframe.D1]

        print("# Indicators Micro-Benchmark (latest closed bar)")
        print("# Symbols:", ", ".join(symbols))
        print("# Timeframes:", ", ".join(tf.value for tf in timeframes))
        print()

        for sym in symbols:
            for tf in timeframes:
                try:
                    bars = get_ohlc_data(sym, tf, 300)
                except Exception as e:
                    print(f"[WARN] {sym} {tf.value}: fetch error: {e}")
                    continue

                closed = [b for b in bars if getattr(b, "is_closed", None) is not False]
                if len(closed) < 220:
                    print(f"[WARN] {sym} {tf.value}: insufficient closed bars ({len(closed)})")
                    continue
                closes = [b.close for b in closed]
                highs = [b.high for b in closed]
                lows = [b.low for b in closed]

                print(f"{sym} {tf.value}")

                # RS I(14)
                t0 = time.perf_counter()
                rsi = ind.rsi_latest(closes, 14)
                t1 = time.perf_counter()
                print(f"  RSI(14): {format_ms((t1 - t0) * 1000)} value={rsi if rsi is not None else 'n/a'}")

                # EMA 21/50/200
                for p in (21, 50, 200):
                    t0 = time.perf_counter()
                    v = ind.ema_latest(closes, p)
                    t1 = time.perf_counter()
                    print(f"  EMA({p}): {format_ms((t1 - t0) * 1000)} value={v if v is not None else 'n/a'}")

                # MACD(12,26,9)
                t0 = time.perf_counter()
                macd = ind.macd_latest(closes, 12, 26, 9)
                t1 = time.perf_counter()
                print(
                    f"  MACD(12,26,9): {format_ms((t1 - t0) * 1000)} value={macd if macd is not None else 'n/a'}"
                )

                # UTBot (EMA50, ATR10, k=3)
                t0 = time.perf_counter()
                ut = ind.utbot_latest(highs, lows, closes, 50, 10, 3.0)
                t1 = time.perf_counter()
                print(
                    f"  UTBot(50,10,k=3): {format_ms((t1 - t0) * 1000)} value={ut if ut is not None else 'n/a'}"
                )

                # Ichimoku (9/26/52, disp=26)
                t0 = time.perf_counter()
                ichi = ind.ichimoku_latest(highs, lows, closes, 9, 26, 52, 26)
                t1 = time.perf_counter()
                print(
                    f"  Ichimoku(9/26/52): {format_ms((t1 - t0) * 1000)} value={ichi if ichi is not None else 'n/a'}"
                )

        return 0
    finally:
        _shutdown_mt5(mt5)


if __name__ == "__main__":
    raise SystemExit(main())


