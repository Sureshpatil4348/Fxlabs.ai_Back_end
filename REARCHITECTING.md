# Re-Architecting: Polling-Based MT5 Market Data and Indicator Streaming (No EA)

This document defines a simple, polling-only design that uses Python’s MetaTrader5 library to deliver fast tick streaming and closed-bar indicator updates on a 10-second cadence. No Expert Advisor (EA) or external bridge is required.

## Goals
- Minimal frontend data: push only what’s needed. Ticks are pushed on a fixed ~500 ms cadence (2 Hz), coalesced; not on every tick. A lightweight daily % change is included within each tick payload.
- Every 10 seconds, detect newly closed candles for all tracked symbols and timeframes (M1 → W1) and emit indicator updates (planned addition).
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
  - A tick-driven loop in `server.py` reads `mt5.symbol_info_tick` for subscribed symbols and pushes `{"type":"ticks","data":[...]}` batches over WebSocket when a new tick timestamp appears per symbol (duplicates coalesced).
  - Caches the last sent tick timestamp per symbol to avoid duplicates.

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
  - Price stream: pushes `ticks` messages on new-tick arrival (coalesced). Daily % change is included in tick payloads.
  - Indicators stream: pushes `indicator_update` when new closed-bar indicators are computed (10s poll cadence; v2-only).
  - On subscribe, server sends `initial_ohlc` when `ohlc` is requested. `initial_indicators` will be added with the indicator pipeline.
- WebSocket v2 (`/market-v2`) — Broadcast-All Mode
  - No explicit subscriptions required. Server broadcasts ticks and indicator updates for a baseline set of symbols/timeframes to all connected clients.
  - Baseline symbols: broker-suffixed `RSI_SUPPORTED_SYMBOLS` from `app/constants.py`.
  - Baseline timeframes: M1, M5, M15, M30, H1, H4, D1.
  - Subscription messages are still accepted for compatibility but not required in v2.

## Data Models
- Tick (frontend): `{symbol, time, time_iso, bid, ask, last, volume, flags, daily_change_pct}`
- IndicatorSnapshot: `{sym, tf, bar_time, indicators: { rsi: {period->value}, ema: {21,50,200}, macd: {macd, signal, hist}, ichimoku: {tenkan, kijun, senkou_a, senkou_b, chikou}, utbot: {signal, type, baseline, atr, longStop, shortStop, new, confidence} }}`

Daily % change calculation (matching MT5 as closely as feasible without EA):
- Prefer the current D1 bar open for the broker’s trading day: `daily_change_pct = 100 * (bid_now - D1_open_today) / D1_open_today`.
- Implementation: fetch last 2 D1 bars via `get_ohlc_data(symbol, Timeframe.D1, 2)`. If the latest D1 bar’s `time` belongs to today (broker server time), use its `open`; otherwise use the previous D1 bar’s `close` as fallback for session transitions.
- Use `bid` basis consistently for both numerator and denominator to minimize drift.

## WebSocket API and Message Formats

 - Endpoint
  - `/ws/market` (unified)
  - `/market-v2` (new, versioned WS — runs alongside `/ws/market` during migration)

- Server greeting
  - On connect, server sends:
    ```json
    {
      "type": "connected",
      "message": "WebSocket connected successfully",
      "supported_timeframes": ["1M","5M","15M","30M","1H","4H","1D","1W"],
      "supported_data_types": ["ticks","ohlc"],
      "supported_price_bases": ["last","bid","ask"],
      "ohlc_schema": "parallel"
    }
    ```

- Subscribe
  - Client → server to start streaming for a symbol×timeframe:
    ```json
    {
      "action": "subscribe",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data_types": ["ticks","ohlc"],
      "price_basis": "last",
      "ohlc_schema": "parallel"
    }
    ```
  - Server → client confirmation:
    ```json
    {
      "type": "subscribed",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data_types": ["ticks","ohlc"],
      "price_basis": "last",
      "ohlc_schema": "parallel"
    }
    ```
  - Errors are returned as `{ "type": "error", "error": "..." }`.

- Snapshots on subscribe
  - Initial OHLC (latest cached series), when `ohlc` is requested:
    ```json
    {
      "type": "initial_ohlc",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data": [ /* array of OHLC objects with parallel Bid/Ask fields and is_closed */ ]
    }
    ```
  - Initial Indicators (latest closed-bar snapshot), when `indicators` is requested:
    ```json
    {
      "type": "initial_indicators",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data": {
        "bar_time": 1696229940000,
        "indicators": {
          "rsi": {"14": 51.23},
          "ema": {"21": 1.06871, "50": 1.06855, "200": 1.06780},
          "macd": {"macd": 0.00012, "signal": 0.00010, "hist": 0.00002}
        }
      }
    }
    ```

- Live pushes
  - Ticks (coalesced by symbol):
    ```json
    { "type": "ticks", "data": [ {"symbol":"EURUSDm","time":1696229945123,"time_iso":"2025-10-02T14:19:05.123Z","bid":1.06871,"ask":1.06885,"volume":120, "daily_change_pct": -0.12}, ... ] }
    ```
  - OHLC updates: not pushed in v2. OHLC is used server-side only for indicator calculations, alerts, and debugging. Legacy `/ws/market` still supports OHLC streaming.
  - Indicator update:
    ```json
    {
      "type": "indicator_update",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data": {
        "bar_time": 1696229940000,
        "indicators": {
          "rsi": {"14": 51.23},
          "ema": {"21": 1.06871, "50": 1.06855, "200": 1.06780},
          "macd": {"macd": 0.00012, "signal": 0.00010, "hist": 0.00002}
        }
      }
    }
    ```
    - Note: `bar_time` is epoch milliseconds (ms) using broker server time.

Broadcast-All Notes (v2 only)
- Indicators are broadcast to all v2 clients for the baseline coverage; no `data_types:["indicators"]` is needed.
- Ticks are pushed for all baseline symbols; OHLC is not streamed on v2.
- Clients can still send `subscribe`/`unsubscribe`, but server behavior in v2 does not require them for receiving baseline data.

- Unsubscribe and keepalive
  - Unsubscribe a single symbol×timeframe:
    ```json
    { "action": "unsubscribe", "symbol": "EURUSDm", "timeframe": "5M" }
    ```
    Server confirms with `{ "type": "unsubscribed", "symbol": "EURUSDm", "timeframe": "5M" }`.
  - Ping/pong:
    - Client: `{ "action": "ping" }`
    - Server: `{ "type": "pong" }`

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

## Market v2 WebSocket — `/market-v2` (Backwards-Compatible Rollout)

- Introduce a stable, forward‑compatible WebSocket endpoint that serves real‑time ticks and indicator streaming. OHLC is computed/cached server‑side only (not streamed in v2).
- Keep existing endpoints (`/ws/market`, `/ws/ticks`) operational during migration; remove them after successful client cutover.

Endpoint
- New WebSocket path: `/market-v2`
- Greeting (capabilities only):
  ```json
  {
    "type": "connected",
    "message": "WebSocket connected successfully",
    "supported_timeframes": ["1M","5M","15M","30M","1H","4H","1D","1W"],
    "supported_data_types": ["ticks","indicators"],
    "supported_price_bases": ["last","bid","ask"]
  }
  ```

Broadcast-All Behavior
- Server pushes: `ticks` and `indicator_update` for baseline symbols/timeframes without subscription.
- Optional: clients may still `subscribe` to receive `initial_indicators` snapshots for specific symbol×timeframe on demand. `initial_ohlc` is not sent on v2.

Live Push Types
- `ticks`: coalesced list, as in v1
- `indicator_update`: closed‑bar indicators after 10s poller detects a new bar

Validation & Safety
- Strict symbol/timeframe allowlist; per‑connection caps on total subscriptions.
- Optional API token binding at WS level (same header policy as REST, if enabled).

Migration Plan
1) Implement `/market-v2` directly (no feature flag needed as app is not live). Keep legacy endpoints available during testing.
2) Capability discovery: v2 greeting advertises `supported_data_types` and `ohlc_schema`. Clients can detect `indicators` support directly from `supported_data_types`.
3) Soak test: Mirror a subset of symbols/TFs on both endpoints; compare volumes and error rates. Add metrics for per‑type send counts and failures.
4) Client rollout: Frontend migrates to `/market-v2` first for read‑only features; enable indicators/summary per module.
5) Deprecation window: Emit a one‑line deprecation notice to v1 clients in the greeting (`note: "deprecated; use /market-v2"`). Announce removal date.
6) Removal: After adoption ≥ 100%, delete `/ws/ticks` and `/ws/market` routes and related legacy glue.

Breaking Changes vs v1 (none required)
- Message envelope and OHLC payloads remain identical; v2 only adds new type `indicator_update`.
- Clients not using new types are unaffected beyond the path change.

Operational Notes
- Use sensible code defaults for polling cadence and indicator streaming during initial rollout.
- Ensure graceful disconnect handling is preserved; do not send after close.

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

## Alerts and Live RSI Debugging — Single Source of Truth

All alert engines and live RSI debugging must reuse the same indicator pipeline to ensure consistent numbers and behavior across the system.

- Single pipeline
  - Compute indicators only in `app/indicators.py` and populate `app/indicator_cache.py`.
  - Consumers (alerts, WS, debug logs) read from `indicator_cache` instead of re-computing.

- RSI Tracker Alert
  - Use `indicator_cache[(symbol,timeframe)]` to read the latest closed-bar RSI value for the configured period (e.g., 14).
  - Maintain a small ring buffer in `indicator_cache` so last N RSI values are available for cross detection and warm-up.

- RSI Correlation Tracker
  - For each pair, read RSI series from the ring buffer for both symbols for the selected timeframe and period.
  - If the buffer is not yet warm (insufficient bars), temporarily fall back to computing the series via `app/indicators.rsi_wilder` using the same OHLC source, then cache the last value to converge quickly.
  - Always enforce closed-bar gating (no forming candles).

- Heatmap / Quantum Tracker
  - Replace per-cycle computations with reads of cached EMA/MACD/UTBot/Ichimoku values and apply only the scoring/aggregation step.
  - If any indicator component is missing (e.g., in early warm-up), treat as neutral for that cell.

- Indicator Tracker
  - Use cached EMA/RSI (and other requested indicators) to determine flips; do not recompute.

- Live RSI Debugging
  - Emit logs from the same cache: when `indicator_update` is produced for 5M, log the RSIclosed value and OHLC summary for that closed bar.
  - Do not perform separate fetches or computations in the debug path; reuse cache values for parity.
  - Rate-limit debug prints if needed to avoid spam (e.g., log only on new 5M bar).

- Benefits
  - One source of truth across UI, alerts, and logs; eliminates drift.
  - Lower CPU and fewer MT5 IPC calls; predictable behavior under load.

## Parity With MT5 (Can We Match Exactly?)

Short answer: not guaranteed to be bit-for-bit identical across all indicators without using MT5’s own indicator handles. However, we can achieve near-parity with careful calibration.

Why exact parity is hard without MT5 handles:
- Initialization/Seeding: EMA, MACD, and RSI depend on initial seed choices (simple average vs price start), which may differ from MT5 internal implementations.
- Rounding and Precision: MT5 may round or maintain precision differently at each step.
- Price Basis and Time Alignment: Ensure we consistently use closes (or bid/ask if desired) and aligned broker timestamps; mismatches introduce drift.
- Proprietary nuances: Some MT5 implementations include subtle buffering/offset logic not publicly documented.

Expected deviations and tolerances (closed-bar values unless noted)
- Bid price stream (500ms): values come from `mt5.symbol_info_tick` and should match MT5 Market Watch bid exactly at the same moment; minor differences can occur due to transmission delay and local rounding. Tolerance: 0 pips ideally; up to 1 pip during fast markets.
- Daily % change (planned summary field): depends on broker session convention (today’s D1 open vs prior close) and price basis (bid vs last). Using bid and D1 open yields Tolerance: ≤ 0.05% (5 bps). During session rollover or sparse-tick periods, temporary deviations up to 0.10% may appear.
- RSI (Wilder): with SMA seeding and closed bars, typical absolute error ≤ 0.05; edge cases (low volatility, very short periods) up to 0.15.
- EMA 21/50/200: with SMA seed, absolute difference generally ≤ 1e-4 on FX quotes; for 5-digit pairs, ≤ 1–2 pips × 1e-3 (i.e., 0.00010). Prefer reporting to 5 decimals.
- MACD (12,26,9): MACD and signal lines close to EMA tolerances; histogram difference within ≤ 2e-4 typically. Accept up to 5e-4 in high-volatility or short-history warm-up.
- Ichimoku (9/26/52): deterministic given OHLC highs/lows; expect identical values if the OHLC basis matches (bid). If MT5 chart uses ask/mid or custom offsets, expect ≤ 1 pip deviation on Tenkan/Kijun and derived spans.
- UT Bot (EMA baseline + ATR Wilder):
  - baseline/atr/stops: rounding to 5 decimals; absolute difference typically ≤ 0.00005 on majors.
  - signal/flip: identical on closed bars except when close sits within ≤ 1e-5 of the stop; treat these as boundary-equivalent; differences should be rare and non-material.
- Bar time (`bar_time`): sourced from MT5 broker server time; equality expected. Indicator push latency does not change the bar timestamp.

Calibration plan:
- Create parity tests that pull the last N closed bars from MT5 and compare Python outputs to target chart values within tolerances (e.g., EMA/RSI/MACD absolute error thresholds above).
- Lock price basis (close vs bid/ask) and ensure `is_closed` filtering is applied uniformly.
- Round outbound values consistently (e.g., 5 decimals for UT Bot numeric fields) to match UI.

Conclusion: We can get very close across indicators on closed bars, but absolute, universal equality with MT5 charts is not guaranteed without using MT5-native indicator handles.

## Implementation Checklist (Source of Truth)

| Step | ID | Area | Task | Owner | Status | Definition of Done | Files/Modules | Dependencies | Notes |
|---:|---|---|---|---|---|---|---|---|---|
| 01 | WS-V2-1 | WebSocket v2 | Add `/market-v2` endpoint | Backend | DONE | Endpoint serves ticks/ohlc; advertises capabilities | `server.py`,`README.md` | None | No feature flag needed |
| 02 | WS-1 | WebSocket | Extend v2 greeting to advertise `indicators` | Backend | DONE | `connected` includes `indicators` | `server.py` | WS-V2-1 | Backward compatible |
| 02A | WS-V2-6 | WebSocket v2 | Broadcast-all mode (baseline sym×TF coverage) | Backend | DONE | v2 clients receive all baseline data without subscribing | `server.py`,`REARCHITECTING.md` | WS-V2-1 | Subscriptions optional |
| 03 | IND-1 | Indicators | Create `app/indicators.py` (RSI/EMA/MACD/Ichimoku/UT Bot) | Backend | DONE | Matches tolerances; docstrings | `app/indicators.py` | None | Centralized math |
| 04 | CACHE-1 | Indicators | Add `app/indicator_cache.py` with deque per (sym,tf) | Backend | DONE | `get_latest_*`,`update_*`; ring size cfg | `app/indicator_cache.py` | IND-1 | Async-safe usage |
| 05 | SCHED-1 | Scheduler | 10s closed-bar detector/poller | Backend | DONE | Detects, computes, stores, broadcasts | `server.py` | IND-1, CACHE-1 | Measured latency logged |
| 06 | WS-2 | WebSocket | Handle `data_types` incl. `indicators` on subscribe | Backend | DONE | Accept/validate; send snapshot+updates | `server.py` | SCHED-1 | Per-client subs |
| 07 | WS-3 | WebSocket | Add `initial_indicators` + `indicator_update` shapes | Backend | DONE | JSON contracts finalized | `server.py`,`REARCHITECTING.md` | IND-1,SCHED-1 | Include `bar_time` ms |
| 08 | WS-V2-2 | WebSocket v2 | Remove `market_summary` (daily_change_pct only in ticks) | Backend | DONE | No separate summary pushes; keep daily_change in tick payloads | `server.py`,`README.md` |  |  |
| 09 | DEBUG-1 | Debug | Align liveRSI to cache; single source numbers | Backend | DONE | Log when 5M indicator updates | `server.py`,`app/mt5_utils.py` | SCHED-1 | Removed duplicate helper; logs from indicator scheduler |
| 10 | OBS-1 | Observability | Add metrics + structured logs | Backend | DONE | Poll durations; items; latencies | `server.py` | SCHED-1 | JSON logs optional |
| 11 | SEC-1 | Security | WS input validation + allowlists | Backend | DONE | Validate symbol/tf; caps; optional auth | `server.py` | None | Mirrors REST auth policy: optional `X-API-Key` on WS; allowlists and caps enforced |
| 12 | ALERT-1 | Alerts | Refactor RSI Tracker to read cache | Backend | DONE | No re-compute; closed-bar only | `app/rsi_tracker_alert_service.py` | CACHE-1 | Keep cooldown logic |
| 13 | ALERT-2 | Alerts | Refactor RSI Correlation to read cache | Backend | DONE | Ring buffers; warm-up fallback | `app/rsi_correlation_tracker_alert_service.py` | CACHE-1 | Pair handling; reads `indicator_cache.get_latest_rsi` with warm-up fallback (compute + `update_rsi`) |
| 14 | ALERT-3 | Alerts | Refactor Heatmap (Quantum) to read cache | Backend | DONE | Cache → aggregation only | `app/heatmap_tracker_alert_service.py` | CACHE-1 | Quiet-market damping |
| 15 | ALERT-4 | Alerts | Refactor Indicator Tracker to read cache | Backend | DONE | Flip detection from cache | `app/heatmap_indicator_tracker_alert_service.py` | CACHE-1 | K=3 window |
| 16 | IND-2 | Indicators | Add micro-bench + unit checks | Backend | DONE | 3–5 symbols×TFs parity | `tests/` | MT5 running | No net installs |
| 17 | PAR-1 | Parity | Add parity checks within tolerances | Backend | DONE | Compare last N closed bars via `tests/test_parity.py` for 3–5 symbols×TFs; enforce RSI ≤ 0.15 abs diff, EMA tail ≤ 1e-9, MACD hist ≤ 5e-4; daily_change_pct (Bid) parity ≤ 0.10% | `tests/test_parity.py` | IND-1 | Close vs Bid fixed; includes daily % change parity |
| 18 | WS-V2-3 | WebSocket v2 | Dual-run + metrics/soak | Backend | DONE | Per-type counters; periodic reporter; low error rate | `server.py` | WS-V2-1 | Compare vs v1 via `obs.ws` logs (ws_metrics) |
| 19 | ROLL-1 | Rollout | Gradual enablement; measure CPU/latency | Backend | DONE | Start 10×3; ramp with env overrides; CPU/time logged | `server.py`,`README.md` | SCHED-1 | Env: `INDICATOR_ROLLOUT_MAX_SYMBOLS`, `INDICATOR_ROLLOUT_TFS`, `INDICATOR_ROLLOUT_SYMBOLS` |
| 20 | WS-V2-4 | WebSocket v2 | Client migration docs + v1 deprecation notice | Backend | TODO | Banner + timeline | `server.py`,`README.md` | WS-V2-1 | Short removal window |
| 21 | WS-V2-5 | WebSocket v2 | Remove `/ws/ticks` and `/ws/market` after cutover | Backend | TODO | Delete routes/legacy glue | `server.py` | WS-V2-3/4 | Keep README updated |
| 22 | DOC-1 | Docs | Keep README.md updated | Backend | DONE | README references this doc | `README.md` | None | Clarify current vs planned |
| 23 | DOC-2 | Docs | Keep ALERTS.md aligned to pipeline | Backend | DONE | liveRSI note reflects task | `ALERTS.md` | None | No math duplication |
| 24 | WS-V2-7 | WebSocket v2 | Remove per-client subscription model post cutover | Backend | TODO | Delete subscribe/unsubscribe handlers; remove `SubscriptionInfo`-based gating; v2 stays broadcast-only | `server.py`,`app/models.py` | WS-V2-5 | Keep ping/pong; retain global shaping defaults |
| 25 | DOC-3 | Docs | Update examples to broadcast-only (remove sub flows) | Backend | TODO | Update `README.md` and `test_websocket.html` to reflect v2 broadcast; note legacy endpoints until removal | `README.md`,`test_websocket.html` | WS-V2-7 | Keep optional snapshot subscribe notes |
