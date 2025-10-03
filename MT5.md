# MT5 Integration, Data Flow, WebSocket Streaming, Alerts, and Live RSI Debugging

This document explains exactly which MT5 integration is used, how market data is fetched and streamed to the frontend via WebSocket, how alert calculations work, and how live RSI debugging is implemented.

## MT5 Integration
- Library: `MetaTrader5==5.0.45` (see `requirements.txt`).
- Initialization: FastAPI lifespan initializes MT5 using optional `MT5_TERMINAL_PATH`.
  - Code: `server.py:22` (import/availability), `server.py:76` (initialize), `app/config.py:13` (`MT5_TERMINAL_PATH`).
- Timeframe mapping: `app/mt5_utils.py:12` maps `app.models.Timeframe` to MT5 constants.

## Data Fetch (MT5 → Backend)
- Symbol selection: `ensure_symbol_selected(symbol)` ensures symbol exists and is visible; returns descriptive 4xx/5xx on failure.
  - Code: `app/mt5_utils.py:18`.
- Ticks: `get_current_tick(symbol)` wraps `mt5.symbol_info_tick` and normalizes to `app.models.Tick`.
  - Code: `app/mt5_utils.py:44`.
- OHLC bars: `get_ohlc_data(symbol, timeframe, count)` uses `mt5.copy_rates_from_pos` and converts bars to `app.models.OHLC` via `_to_ohlc`.
  - Code: `app/mt5_utils.py:81` (fetch) and `app/mt5_utils.py:54` (convert).
- Closed-bar gating: `_to_ohlc` computes `is_closed` from timeframe boundary; RSI and alerts only use closed bars.
  - Code: `app/mt5_utils.py:66` and `app/rsi_utils.py:33` (`closed_closes`).
- Caching: lightweight in-memory cache keyed by `symbol × timeframe` with `update_ohlc_cache` and `get_cached_ohlc` to reduce MT5 calls.
  - Code: `app/mt5_utils.py:156` (update cache), `app/mt5_utils.py:170` (read cache).

Notes:
- If `copy_rates_from_pos` returns empty, a debug log is emitted; callers gracefully handle an empty list.
- Canonical OHLC conversion, caching, and scheduling live in `app/mt5_utils.py`. WebSocket and alert services use these helpers (no duplicate implementations).

## WebSocket Streaming (Backend → Frontend)
- Endpoints:
  - New: `/ws/market` (preferred). Code: `server.py:1136`.
  - Legacy: `/ws/ticks` (tick-only). Code: `server.py:1086`.
- Client flow (new `/ws/market`):
  1) Connect → server sends a `connected` message with supported options.
  2) Send `{ action: "subscribe", symbol, timeframe, data_types, price_basis, ohlc_schema }`.
  3) Server responds with `subscribed` and an `initial_ohlc` snapshot (if requested).
  4) Live updates:
     - Ticks: compact binary JSON `{ type: "ticks", data: [...] }` up to ~1 Hz (≈1000 ms cadence).
     - Scheduled OHLC boundary: `{ type: "ohlc_update", data: { ... } }` once per timeframe close.
- Scheduling: Next OHLC boundary is computed with `calculate_next_update_time`, and at boundary the cache is refreshed and a closed bar is pushed.
  - Code: `server.py:879` (send loop), `app/mt5_utils.py:116` (boundary calc).
- Data shaping:
  - `price_basis`: `last|bid|ask` (see `app/models.py:21`).
  - `ohlc_schema`:
    - `parallel` (default): includes `open/high/low/close` plus `openBid/openAsk/...` when derivable from spread.
    - `basis_only`: canonical `open/high/low/close` reflect the requested basis and parallel fields are omitted.
  - OHLC parallel fields are computed centrally in `app/mt5_utils._to_ohlc` by splitting `spread` across bid/ask using the symbol `point`, with a tick-based fallback when spread is absent.
  - Formatting code: `server.py:715` and `server.py:728`.

## Alerts Calculation (Closed-Bar RSI and Correlation)
- Scheduler: Every 5 minutes the minute scheduler refreshes the alert cache and evaluates all alert types.
  - Code: `server.py:1000` (look for `_minute_alerts_scheduler`).
- RSI Tracker (single alert per user):
  - Closed-bar RSI(14 by default) using Wilder smoothing; triggers on threshold crossings with per-side hysteresis re-arm.
  - Enforces minimum `5M` timeframe for alerts; warm-up and per-user closed-bar gating prevent duplicate triggers.
  - Code: `app/rsi_tracker_alert_service.py` (key methods `_get_recent_rsi_series`, `_detect_rsi_crossing`, `check_rsi_tracker_alerts`).
- RSI Correlation Tracker:
  - Two modes: `rsi_threshold` mismatch vs. `real_correlation` (rolling log-return correlation window).
  - Closed-bar gating and per-user state tracking; triggers logged and optionally emailed.
  - Code: `app/rsi_correlation_tracker_alert_service.py`.
- Shared RSI math: `app/rsi_utils.py` (`calculate_rsi_series`, `calculate_rsi_latest`, `closed_closes`).

For end-to-end alert behavior and product policies, see `ALERTS.md`.

## Live RSI Debugging
- Toggle: `LIVE_RSI_DEBUGGING=true` (default `false`). Code: `app/config.py`.
- Behavior: On each new closed 5‑minute candle for `BTCUSDm`, logs a concise line including RSI(14) on closed bars, candle timestamp, OHLC, volume, tick volume, and spread.
  - Emission location: indicator scheduler within `server.py` (gated to `BTCUSDm` and `5M`).
- Reference doc: `LIVE_RSI_DEBUGGING.md` (how to enable and sample output).

## REST Endpoints (MT5-backed)
- `GET /api/ohlc/{symbol}?timeframe=5M&count=250` → returns latest OHLC bars.
- `GET /api/tick/{symbol}` → returns latest tick.
- Both require `X-API-Key` if `API_TOKEN` is set. Code: `server.py:472` (auth helper) and endpoints at `server.py:518`, `server.py:540`.

## Security & Operational Notes
- REST endpoints can be gated with `API_TOKEN`; WebSocket endpoints are currently open (consider token-based auth if needed).
- Symbol validation is enforced centrally; failures return actionable messages (sample/nearby symbols when unknown).
- On shutdown, MT5 is cleanly shut down via lifespan teardown.
 - A single in-process MT5 session is shared across WebSocket streaming, alert schedulers, and live RSI debugging to ensure data parity.

## Troubleshooting
- "No rates from MT5" → check MT5 is running, logged-in, and the symbol is enabled in Market Watch.
- "Unknown symbol" → use `/api/symbols?q=...` to discover the exact broker-suffixed symbol (e.g., `EURUSDm`, `BTCUSDm`).
- Empty RSI/alerts → ensure timeframe >= `5M` for alerts and that closed bars exist (warm-up requires > period bars).
