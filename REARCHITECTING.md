# Re-Architecting: Polling-Based MT5 Market Data and Indicator Streaming (No EA)

This document defines a simple, polling-only design that uses Python’s MetaTrader5 library to deliver fast tick streaming and closed-bar indicator updates on a 10-second cadence. No Expert Advisor (EA) or external bridge is required.

## Goals
- Live tick pricing to frontend (target ~100ms stream cadence, best-effort) via WebSocket.
- Every 10 seconds, detect newly closed candles for all tracked symbols and timeframes (M1 → W1) and emit indicator updates.
- Indicators computed in Python for closed bars:
  - RSI (support common periods: e.g., 2, 3, 5, 7, 9, 14, 21, 50)
  - EMA 21, EMA 50, EMA 200
  - MACD (12,26,9)
  - UT Bot (EMA baseline + ATR stops; flip detection K=3 as per product spec)
  - Ichimoku (9/26/52)
- Maintain an in-memory cache to provide instant snapshots on WebSocket connect.
- Keep components lean, observable, and secure. Do not optimize for backward compatibility.

## Non-Goals
- Historical persistence or long-term storage.
- Perfect (bit-for-bit) parity with MT5 chart values for all indicators (see Parity section).
- Multi-tenancy concerns are out of scope for this re-architecture.

## High-Level Architecture (Polling Only)

- MT5 Python Integration (no EA)
  - Use `MetaTrader5` Python package to fetch ticks and OHLC bars.
  - Map timeframes via `app/mt5_utils.MT5_TIMEFRAMES`.

- Tick Streaming (existing)
  - A 10Hz loop in `server.py` reads `mt5.symbol_info_tick` for subscribed symbols and pushes `ticks` over WebSocket.
  - Cache the last sent tick timestamp per symbol to avoid duplicates.

- Closed-Bar Indicator Poller (new, 10s cadence)
  - Background task runs every 10 seconds.
  - For each tracked `symbol×timeframe`:
    - Fetch last 2–3 bars using `mt5.copy_rates_from_pos` (via `app/mt5_utils.get_ohlc_data`).
    - Detect a new closed bar by comparing the last closed `time` with the previously cached value.
    - Compute indicators on the last closed bar using Python helpers.
    - Update an in-memory `indicator_cache` and push `indicator_update` to WebSocket subscribers.

- Caches
  - `ohlc_cache` (existing): maintained via `update_ohlc_cache/get_cached_ohlc`.
  - `indicator_cache` (new): dictionary keyed by `symbol:timeframe`, storing the latest IndicatorSnapshot (+small ring buffer for snapshots on connect).

- WebSocket (existing `/ws/market`)
  - Continue sending `ticks` (10Hz) and `ohlc_update` (at TF boundaries).
  - Add `indicator_update` messages for new closed bars and send a snapshot on connect.

## Data Models
- PriceTick: `{sym, ts, bid, ask, last?, spread?, seq?}` (unchanged)
- IndicatorSnapshot: `{sym, tf, bar_time, indicators: { rsi: {period->value}, ema: {21,50,200}, macd: {macd, signal, hist}, ichimoku: {tenkan, kijun, senkou_a, senkou_b, chikou}, utbot: {signal, type, baseline, atr, longStop, shortStop, new, confidence} }}`

## Implementation Plan (Map to Current Code)

1) Indicator Helpers
   - Add `app/indicators.py` with canonical Python implementations:
     - `rsi_wilder(closes, period)` compatible with `app/rsi_utils` or reuse it directly.
     - `ema(closes, period)`.
     - `macd(closes, fast=12, slow=26, signal=9)` returning triple `(macd, signal, hist)` aligned to the latest closed bar.
     - `ichimoku(ohlc_bars, 9,26,52)` returning `{tenkan,kijun,senkou_a,senkou_b, chikou}`.
     - `utbot(bars, ema_len, atr_len, atr_mult, min_atr_threshold, k_flip=3)` per ALERTS.md parity section.

2) Indicator Cache (new)
   - Create `app/indicator_cache.py`:
     - `indicator_cache[(symbol, timeframe)] -> deque(maxlen=K)` of snapshots.
     - Helpers: `get_latest_indicator_snapshot(symbol, timeframe)`, `update_indicator_snapshot(...)`.

3) 10s Poller Task
   - In `server.py` lifespan (near other schedulers), start `indicator_scheduler()`:
     - Every 10s: for subscribed `symbol×timeframe`, fetch last bars via `get_ohlc_data`, detect new closed bar, compute indicators via `app/indicators`, store to `indicator_cache`, and push `{"type":"indicator_update","data":...}` to appropriate clients.
     - On client connect/subscribe, send the latest snapshot for requested keys.

4) WebSocket Contract
   - Extend `WSClient` in `server.py` to support `data_types` including `"indicators"` and maintain per-client subscriptions.
   - When a subscription includes `indicators`, send snapshot and subsequent `indicator_update`s.

5) Observability & Safety
   - Metrics: compute duration per poll cycle, number of symbols/timeframes processed, failures.
   - Logs: structured per update with `{sym, tf, bar_time}` and `latency_ms` (poll_time − bar_close_time).
   - Input validation: allowlist symbols and timeframes; limit totals to protect MT5 IPC.

6) Rollout
   - Start with a small set (e.g., 10 symbols × 3 TFs) and measure CPU/latency.
   - Increase coverage gradually; tune poller batch size if needed.

## Deletions and Simplifications (What to Remove/Refactor)

Goal: eliminate scattered indicator math and source-of-truth duplication by centralizing indicator computation and streaming.

- Replace ad-hoc indicator computations in services with reads from `indicator_cache`:
  - `app/heatmap_tracker_alert_service.py` (various EMA/MACD/UTBot/Ichimoku computations). Refactor to consume cached indicators rather than recomputing per alert cycle.
  - `app/heatmap_indicator_tracker_alert_service.py` (EMA/RSI signal flips). Refactor to use cached EMA/RSI values.
  - Keep `app/rsi_utils.py` if other modules rely on it; otherwise, route RSI reads via `indicator_cache`.

- Remove duplicate indicator functions after refactor:
  - Delete in-file helpers (EMA/MACD/ATR/Ichimoku/UTBot) that are moved into `app/indicators.py`.

- WebSocket: do not compute indicators on tick in `server.py`; only update OHLC caches on tick and let the 10s poller own closed-bar indicators.

Suggested sequence:
1) Add `app/indicators.py` and `app/indicator_cache.py`.
2) Implement the 10s poller and `indicator_update` streaming in `server.py`.
3) Refactor services to read from `indicator_cache`.
4) Delete/inline-remove duplicate indicator code in services and keep `rsi_utils` only if strictly needed.

## Parity With MT5 (Can We Match Exactly?)

Short answer: not guaranteed to be bit-for-bit identical across all indicators without using MT5’s own indicator handles. However, we can achieve near-parity with careful calibration.

Why exact parity is hard without MT5 handles:
- Initialization/Seeding: EMA, MACD, and RSI depend on initial seed choices (simple average vs price start), which may differ from MT5 internal implementations.
- Rounding and Precision: MT5 may round or maintain precision differently at each step.
- Price Basis and Time Alignment: Ensure we consistently use closes (or bid/ask if desired) and aligned broker timestamps; mismatches introduce drift.
- Proprietary nuances: Some MT5 implementations include subtle buffering/offset logic not publicly documented.

What we can match closely:
- RSI (Wilder) and EMA: With the same period, close prices, and seeding (SMA for first value), results generally match within rounding on closed bars.
- MACD (12,26,9): Close alignment and consistent EMA seeding keep values very close; histogram may vary by small epsilons.
- Ichimoku (9/26/52): Deterministic given OHLC highs/lows; ensure we use midpoints for Tenkan/Kijun and correct shifts.
- UT Bot: Parity depends on using the same EMA and ATR definitions and your flip logic (we’ll mirror your JS reference and rounding).

Calibration plan:
- Create parity tests that pull the last N closed bars from MT5 and compare Python outputs to target chart values within tolerances (e.g., absolute error ≤ 1e-4 for EMA/RSI/MACD lines).
- Lock price basis (close vs bid/ask) and ensure `is_closed` filtering is applied uniformly.
- Round outbound values consistently (e.g., 5 decimals for UT Bot numeric fields) to match UI.

Conclusion: We can get very close across indicators on closed bars, but absolute, universal equality with MT5 charts is not guaranteed without using MT5-native indicator handles.
