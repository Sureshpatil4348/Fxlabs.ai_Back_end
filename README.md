# Fxlabs.ai Backend - Real-time Market Data Streaming Service

WebSocket v2: Use `/market-v2` for live ticks, indicator updates, quantum analysis updates, and trending pairs updates (hourly broadcast). Legacy endpoints have been removed. Note: As of WS-V2-7, v2 is broadcast-only; `subscribe`/`unsubscribe` are ignored (server replies with an informational message). There are no OHLC or indicator snapshots in v2. Ping/pong is supported for keepalive.

Re-architecture: See `REARCHITECTING.md` for the polling-only MT5 design. Today, the server streams tick and indicator updates over `/market-v2` (tick-driven, coalesced; OHLC is not streamed to clients in v2). No EA or external bridge required.

A high-performance, real-time financial market data streaming service built with Python, FastAPI, and MetaTrader 5 integration. Provides live forex data, OHLC candlestick streaming, AI-powered news analysis, and comprehensive alert systems for trading applications.

Note — FxLabs Prime Domain Update
- All examples and configs now use `fxlabsprime.com`.
- API base URL: `https://api.fxlabsprime.com`
- Frontend origin: `https://app.fxlabsprime.com`
- Email sender: `alerts@fxlabsprime.com`

## 🏗️ Architecture Overview

### System Components

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   MetaTrader 5  │───▶│   FastAPI Server │───▶│  WebSocket/REST │
│   (Data Source) │    │   (Core Engine)  │    │   (API Layer)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌──────────────────┐
                       │  External APIs   │
                       │  (News + AI)     │
                       └──────────────────┘
```

### Key Features

- **Real-time Data Streaming**: Live tick and RSI indicator data via WebSocket (broadcast-only)
- **Cache-first Indicator Access**: REST `/api/indicator` serves latest RSI values from an in-memory cache populated on startup and updated on every closed-candle cycle. Also supports `indicator=quantum` to retrieve per-timeframe and overall Buy/Sell % (signals-only aggregation). Per-indicator entries now include a concise `reason` string explaining the current signal.
- **Historical Data Access**: REST API for historical market data
- **AI-Powered News Analysis**: Automated economic news impact analysis (with live internet search)
- **Comprehensive Alert Systems**: Heatmap and RSI alerts with email notifications
 - Currency Strength alerts: notifies whenever the strongest/weakest fiat currency changes for a configured timeframe
 - **Event‑Driven Alerts**: Alerts are evaluated immediately after the indicator scheduler updates the in‑memory `indicator_cache` on closed bars. A minute scheduler remains as a safety net.
- **Smart Email Cooldown**: Value-based cooldown prevents spam while allowing significant RSI changes (email-level only; RSI Tracker pair-level cooldown removed)
- **Intelligent Caching**: Memory-efficient selective data caching
- **High Performance**: 99.9% bandwidth reduction through selective streaming
- **Scalable Architecture**: Async/await design for high concurrency
- **Per-Pair Concurrency Cap**: Keyed async locks prevent concurrent evaluations for the same pair/timeframe across alert services
- **Warm-up & Stale-Data Protection**: Skips evaluations when latest bar is stale (>2× timeframe) and enforces indicator lookback (e.g., RSI series) before triggering
// Removed: Rate Limits + Digest (alerts send immediately subject to value-based cooldown)
- **IST Timezone Display**: Email timestamps are shown in Asia/Kolkata (IST) for user-friendly readability
  - FxLabs Prime tenant: All alert emails are enforced to IST (Asia/Kolkata) regardless of host tz. If the OS tz database is missing, a robust +05:30 (IST) fallback is applied.
- **Unified Email Header**: All alert emails use a common green header `#07c05c` showing `[FxLabs logo] FxLabs Prime • <Alert Type> • <Local Date IST> • <Local Time IST>` (time in small font)
- **Comprehensive Legal Disclaimer**: All alert emails now include a comprehensive legal disclaimer footer that outlines risks, disclaims financial advice, and links to Terms of Service and Privacy Policy at fxlabsprime.com. This ensures full legal compliance and transparency with users about trading risks.
- **Email Brand Color Update**: We avoid pure black in emails. Any `black`, `#000`/`#000000` is replaced with the brand `#19235d`. Dark grays like `#111827`, `#333333`, and `#1a1a1a` remain for readability and hierarchy.
- **Style‑Weighted Buy Now %**: Heatmap alerts compute a style‑weighted Final Score across selected timeframes and convert it to Buy Now % for triggers, per the Calculations Reference (EMA21/50/200, MACD, RSI, UTBot, Ichimoku; new‑signal boost; quiet‑market damping)
  - Per‑alert overrides: optional `style_weights_override` map customizes TF weights (only applied to selected TFs; invalid entries ignored; defaults used if sum ≤ 0).

## 📐 Calculations Alignment

This backend aligns alert evaluations with the Calculations Reference used by the frontend widgets:

- Closed‑candle policy: All RSI/heatmap evaluations use closed candles; forming candles are not used in triggers.
- Trigger cadence: Event‑driven based on indicator updates; no need to wait for 5‑minute boundary. Closed‑bar gating remains enforced.
- MT5 OHLC snapshots still include the forming candle as the final element with `is_closed=false`. Backend RSI calculations ignore it automatically, so frontend collectors can continue using the tail for live charting without custom trimming.
- RSI (14, Wilder): Computed from MT5 OHLC (Bid‑based series), matching frontend logic. Period is fixed to 14 across the entire system (REST/WS, alerts, emails, cache).
 
- Heatmap/Quantum aggregation:
  - Indicators: RSI(14) plus internal EMA/MACD/UTBot/Ichimoku signals for aggregation only. Exposed via WS `quantum_update` and REST `indicator=quantum`. Non-RSI raw values are not exposed via indicator APIs.
  - New fields: For each timeframe, `indicators` contains per‑indicator `{ signal: buy|sell|neutral, is_new: boolean, reason: string }`. Bottom bar Buy/Sell% is provided under `overall` by style (`scalper`, `swingtrader`).
  - Per‑cell scoring: buy=+1, sell=−1, neutral=0; new‑signal boost ±0.25 in last K=3; quiet‑market damping halves MACD/UTBot cell scores when ATR10 is below the 5th percentile of last 200 values; clamp to [−1.25,+1.25].
  - Aggregation: Σ_tf Σ_ind S(tf,ind)×W_tf×W_ind; Final=100×(Raw/1.25); Buy%=(Final+100)/2; Sell%=100−Buy%.

### Frontend RSI Rendering Guide

Follow these steps to display "RSI (closed)" in the UI for any timeframe/period while still showing live pricing:

1. **Fetch OHLC series** from the backend endpoint. The final bar in the array may have `is_closed=false`; keep it for charting but exclude it from RSI math.
2. **Slice closed bars only:** build a closes list with `[bar.close for bar in bars if bar.is_closed is not False]`. You need at least `period + 1` closed bars before an RSI value is valid.
3. **Apply Wilder smoothing** (same as the backend):
   ```python
   deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
   gains = [max(delta, 0.0) for delta in deltas]
   losses = [max(-delta, 0.0) for delta in deltas]
   avg_gain = sum(gains[:period]) / period
   avg_loss = sum(losses[:period]) / period
   rs = 100.0 if avg_loss == 0 else avg_gain / avg_loss
   rsi_values = [100.0 if avg_loss == 0 else 100 - 100 / (1 + rs)]
   for idx in range(period, len(deltas)):
       avg_gain = ((period - 1) * avg_gain + gains[idx]) / period
       avg_loss = ((period - 1) * avg_loss + losses[idx]) / period
       rs = 100.0 if avg_loss == 0 else avg_gain / avg_loss
       rsi_values.append(100.0 if avg_loss == 0 else 100 - 100 / (1 + rs))
   latest_rsi = rsi_values[-1]
   ```
4. **Per-timeframe support:** reuse the same routine for every timeframe (`5M`, `15M`, ..., `1W`). Just ensure you request enough closed bars (backend uses `period + 10` as a safe margin).
5. **Live price display:** show `bid`/`ask`/`last` from the tick feed or the final forming candle to give users real-time pricing while the RSI stays pinned to the last closed bar.

Following this contract keeps frontend charts, emails, and alert triggers numerically identical.

For runtime inspection of closed-bar RSI, see `LIVE_RSI_DEBUGGING.md` and set `LIVE_RSI_DEBUGGING=true`.

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- MetaTrader 5 terminal installed
- API keys for external services (optional)

### Fresh Repository Setup

#### Step 1: Clone and Navigate
```bash
git clone <repository-url>
cd Fxlabs.ai_Back_end
```

#### Step 2: Create Virtual Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows Command Prompt:
.venv\Scripts\activate

# On Windows PowerShell:
.venv\Scripts\Activate.ps1

# On macOS/Linux:
source .venv/bin/activate
```

#### Step 3: Install Dependencies
```bash
# Upgrade pip first
python -m pip install --upgrade pip

# Install all required packages
pip install -r requirements.txt
```

#### Step 4: Configure Environment (Optional)
```bash
# Copy environment template
cp config.env.example .env

# Edit .env with your configuration (optional for basic functionality)
# The server will work with default settings
```

#### Step 5: Start the Server
```bash
# Start with convenience entrypoints (auto-set TENANT)
python fxlabs-server.py
python hextech-server.py
```

### Alternative: Using Platform Scripts

**Windows (Command Prompt):**
```cmd
start.bat
```

**Windows (PowerShell):**
```powershell
.\start.ps1
```

### Verification

After starting, verify the server is running:

```bash
# Check health status
curl http://127.0.0.1:8000/health

# Expected response:
# {"status": "ok", "mt5_version": "5.0.45"}
```

### Configuration

Create a `.env` file with the following variables. Email credentials are tenant-specific only (no global defaults). Use `fxlabs-server.py` or `hextech-server.py` to select the tenant; you do not need to set `TENANT` manually.

```env
# MT5 Terminal Path (optional)
MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe

# API Token for authentication (required)
API_TOKEN=your_api_token_here

# Allowed origins for CORS (comma-separated)
ALLOWED_ORIGINS=http://localhost:3000,https://yourdomain.com

# Server configuration
HOST=127.0.0.1
PORT=8000

# News Analysis Configuration
PERPLEXITY_API_KEY=your_perplexity_key
# ASOasis Forex Calendar (today; IST)
ASOASIS_API_FOREX_NEWS_ENDPOINT=https://api.asoasis.tech/forex-calender/today?timezone=Asia/Kolkata
ASOASIS_API_FOREX_NEWS_CLIENT_ID=your_client_id
ASOASIS_API_FOREX_NEWS_CLIENT_SECRET=your_client_secret
NEWS_UPDATE_INTERVAL_HOURS=0.5  # 30 minutes
NEWS_CACHE_MAX_ITEMS=500

# Email Configuration (tenant-specific only; no global defaults)
# Define only the variables for the tenant you run.
# FxLabs Prime
FXLABS_SENDGRID_API_KEY=
FXLABS_FROM_EMAIL=alerts@fxlabsprime.com
FXLABS_FROM_NAME=FxLabs Prime Alerts
FXLABS_PUBLIC_BASE_URL=

# HexTech
HEXTECH_SENDGRID_API_KEY=
HEXTECH_FROM_EMAIL=
HEXTECH_FROM_NAME=
HEXTECH_PUBLIC_BASE_URL=

# Supabase (required for alerts and news reminders)
SUPABASE_URL=
SUPABASE_SERVICE_KEY=

# Optional: per-tenant overrides (used by entry scripts and take precedence over base vars)
# FxLabs Prime
FXLABS_SUPABASE_URL=https://your-fxlabs.supabase.co
FXLABS_SUPABASE_SERVICE_KEY=
FXLABS_SENDGRID_API_KEY=
FXLABS_FROM_EMAIL=alerts@fxlabsprime.com
FXLABS_FROM_NAME=FxLabs Prime Alerts
FXLABS_PUBLIC_BASE_URL=https://api.fxlabsprime.com
FXLABS_DAILY_TZ_NAME=Asia/Kolkata
FXLABS_DAILY_SEND_LOCAL_TIME=09:00

# HexTech (placeholders; fill when provisioning HexTech)
HEXTECH_SUPABASE_URL=
HEXTECH_SUPABASE_SERVICE_KEY=
HEXTECH_SENDGRID_API_KEY=
HEXTECH_FROM_EMAIL=
HEXTECH_FROM_NAME=
HEXTECH_PUBLIC_BASE_URL=
HEXTECH_DAILY_TZ_NAME=Asia/Dubai
HEXTECH_DAILY_SEND_LOCAL_TIME=09:00
```

### Daily Morning Brief
- Uses the tenant-specific SendGrid configuration (`FXLABS_*` or `HEXTECH_*`).
- Runs daily at a configurable local time via `daily_mail_scheduler()`.
- Configure timezone and send time using env vars:

```env
# Daily brief schedule
DAILY_TZ_NAME=Asia/Kolkata           # IANA tz (e.g., Asia/Kolkata, UTC, Europe/London)
DAILY_SEND_LOCAL_TIME=09:00          # HH:MM or HH:MM:SS (24h)
```

- The same timezone/time label is shown at the top-right of the email header.
- Recipients are fetched from Supabase Auth (`auth.users`) using the service role key. This is the single source of truth for daily emails and does not depend on per‑product alert tables.
  - Endpoint: `GET {SUPABASE_URL}/auth/v1/admin/users` with `Authorization: Bearer {SUPABASE_SERVICE_KEY}`
  - Pagination: `page`, `per_page` (defaults: 1..N, 1000 per page)
  - The code automatically paginates and deduplicates emails.
  - Core signals in the daily brief use `scalper` mode for Quantum analysis (displayed as "Intraday" in the email).
- For observability, the batch log includes a CSV of recipient emails and count.

#### Daily Brief Duplicate Prevention
- **Date Tracking**: Each scheduler instance tracks the last sent date to prevent duplicate emails on the same day
- **Cooldown Period**: After sending, the scheduler waits 4 hours before re-evaluating, preventing rapid re-triggering
- **Multi-Tenant Support**: **FXLabs** and **HexTech** can run simultaneously in separate processes, each sending their own daily emails independently without interference
  - `python fxlabs-server.py` → sends to FXLabs users at configured IST time
  - `python hextech-server.py` → sends to HexTech users at configured Dubai time
  - Each tenant has its own in-memory tracking and configuration
- **Logging**: All daily email logs include the tenant name for clarity (e.g., `tenant=FXLabs`, `tenant=HexTech`)
- **Per-Instance Protection**: If you accidentally run the same tenant server twice, each instance independently prevents duplicate sends for that tenant on the same date

#### News Reminder Behavior (High‑Impact Only)
- The 5‑minute news reminder filters to only source‑reported high‑impact items (`impact == "high"` from the upstream API). Medium/low impact items are skipped.
- Impact is not AI‑derived for reminders or display; it mirrors the upstream field.
- Branding: News reminder emails now use the same unified green header and common footer as other alerts (logo + date/time in header; single disclaimer footer).

#### Auth Fetch Logging (Verbose)
- Start: `daily_auth_fetch_start | page: 1 | per_page: 1000`
- Per page: `daily_auth_fetch_page | page: <n> | users: <count>`
- Per page emails (debug): `daily_auth_fetch_page_emails | page: <n> | count: <m> | emails_csv: a@x,b@y`
- Final list: `daily_auth_fetch_done | users_total: <k> | emails_csv: ...`
- Daily send mirrors the final list: `daily_auth_emails | users: <k> | emails_csv: ...`

#### Environment Loading (.env)
- The app now auto-loads `.env` via `python-dotenv` in `app/config.py`.
- Place your `.env` at the project root (same folder as `server.py`).
- Existing process environment variables are not overridden (safe-by-default).
- This fixes cases where macOS/Linux sessions didn't see `SENDGRID_API_KEY` unless exported manually.

## 📡 API Documentation

### WebSocket Endpoint

#### Market Data WebSocket v2 (`/market-v2`) — preferred
- **URL**: `ws://localhost:8000/market-v2`
- **Purpose**: Real-time tick and indicator streaming (no OHLC streaming to clients)
- **Behavior**: Broadcast-only baseline (symbols/timeframes). `subscribe`/`unsubscribe` are ignored (server replies with `{type:"info", message:"v2 broadcast-only: subscribe/unsubscribe ignored"}`).
  - As of v2.0.0+, tick updates include all allowed symbols every 1000 ms on a delta basis (only symbols with a new tick since the last send appear in each message).

Tick push payloads to clients remain a list of ticks. Internally, for alert checks, ticks are converted to a map keyed by symbol for consistency across services. Connected discovery message includes capabilities and indicators registry:

```json
{
  "type": "connected",
  "message": "WebSocket connected successfully",
  "supported_timeframes": ["1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W"],
  "supported_data_types": ["ticks", "indicators"],
  "supported_price_bases": ["last", "bid", "ask"]
}
```

Client messages:

```json
{ "action": "ping" }             // server -> { "type": "pong" }
{ "action": "subscribe" }        // server -> { "type": "info", "message": "v2 broadcast-only: subscribe/unsubscribe ignored" }
{ "action": "unsubscribe" }      // same informational response
```

Internal alert tick_data shape:

```json
{
  "timestamp": "2025-09-20T12:34:56.000Z",
  "symbols": ["EURUSDm", "GBPUSDm"],
  "tick_data": {
    "EURUSDm": {"bid": 1.1001, "ask": 1.1003, "time": 1695200096000, "volume": 123},
    "GBPUSDm": {"bid": 1.2501, "ask": 1.2504, "time": 1695200096000, "volume": 456}
  }
}
```

##### WebSocket Disconnect Errors (Expected vs. Actionable)
- You may occasionally see stack traces like:
  - `websockets.exceptions.ConnectionClosedError: no close frame received or sent`
  - `ConnectionClosedOK: received 1001 (going away)`
  - `WebSocketDisconnect(code=1006)`
  - `Cannot call "send" once a close message has been sent.`
- These occur when a client navigates away, a mobile device suspends the tab, or a proxy closes idle connections. The server used to attempt a send during the close handshake, surfacing noisy errors.
- As of this version, background streaming tasks stop gracefully on disconnect and avoid sending after close. You may still see concise disconnect notices in logs; they are harmless.
- Client best practices:
  - Send a proper WebSocket close frame on app shutdown/navigation where possible.
  - Use keepalive/ping if intermediaries are aggressive about idle timeouts.
  - Reconnect with backoff on close codes 1001/1006.

#### Market Data WebSocket v2 (`/market-v2`) — preferred
- Use `/market-v2` for new clients. It exposes tick and indicator payloads only (no OHLC streaming), and advertises capabilities via `supported_data_types` in the greeting.
- Current capabilities: `supported_data_types = ["ticks","indicators"]`.
- Broadcast-All mode: v2 pushes ticks and indicators (closed‑bar) to all connected clients without explicit subscriptions. OHLC is computed server‑side only for indicators/alerts.
  - Symbols: all symbols in `ALLOWED_WS_SYMBOLS` (defaults to all `RSI_SUPPORTED_SYMBOLS` from `app/constants.py`, broker‑suffixed)
- Timeframes: M1, M5, M15, M30, H1, H4, D1, W1
  - Note: `currency_strength` enforces a minimum timeframe of `5M` (no `1M`).
  - Scale: `currency_strength` values are normalized to −100..100 (0 = neutral).
- Subscribe remains optional and is primarily used to receive `initial_ohlc` / `initial_indicators` snapshots on demand.

Security and input validation (mirrors REST policy):
- If `API_TOKEN` is set, WebSocket connections must include header `X-API-Key: <token>`; otherwise connections are allowed without auth.
- Symbols allowlist: by default, all symbols in `RSI_SUPPORTED_SYMBOLS` are accepted and streamed. Override with env `WS_ALLOWED_SYMBOLS` (comma-separated, broker-suffixed) to restrict the feed.
- Timeframe allowlist: defaults to all `app.models.Timeframe` values. Override with env `WS_ALLOWED_TIMEFRAMES` (values like `1M,5M,1H` or enum names like `M1,M5,H1`).
- Per-connection caps (env-configurable):
  - `WS_MAX_SYMBOLS` (default 10)
  - `WS_MAX_SUBSCRIPTIONS` total symbol×timeframe pairs (default 32)
  - `WS_MAX_TFS_PER_SYMBOL` per symbol (default 7)
  - Violations return `{ "type": "error", "error": "..._limit_exceeded" }` or `forbidden_symbol` / `forbidden_timeframe`.

V2 greeting example (capabilities + indicators registry):

```json
{
  "type": "connected",
  "message": "WebSocket connected successfully",
  "supported_timeframes": ["1M","5M","15M","30M","1H","4H","1D","1W"],
  "supported_data_types": ["ticks","indicators"],
  "supported_price_bases": ["last","bid","ask"],
  "note": "v2 endpoint; v1 deprecated — migrate to /market-v2",
  "removal_date": "2025-10-10",
  "removal_date_utc": "2025-10-10T00:00:00Z",
  "indicators": {
    "rsi": {"method": "wilder", "applied_price": "close", "periods": [14]}
  }
}
```
Tick payloads include `daily_change_pct` (Bid vs broker D1 reference) and only contain bid prices:

```json
{"type": "ticks", "data": [ {"symbol":"EURUSDm","time":1696229945123,"time_iso":"2025-10-02T14:19:05.123Z","bid":1.06871, "daily_change_pct": -0.12} ] }
```


##### Indicator payloads (broadcast-only)

Live push when a new bar is detected by the 10s poller:

```json
{
  "type": "indicator_update",
  "symbol": "EURUSDm",
  "timeframe": "5M",
  "data": {
    "bar_time": 1696229940000,
    "indicators": { "rsi": {"14": 51.23} }
  }
}
```

Currency Strength updates are also pushed over WebSocket on closed bars only and only for WS-allowed timeframes (minimum `5M`).

Server logs: On each new closed-bar currency strength broadcast, the server logs an INFO line on logger `obs.curstr` with the timeframe, bar_time, and the JSON map of strengths, for example:

```
📊 currency_strength_update | tf=5M bar_time=1696229940000 values={"USD":23.5,"EUR":-12.2,"GBP":8.7,"JPY":-31.4,"AUD":15.9,"CAD":2.1,"CHF":-5.6,"NZD":-1.0}
```
Logs are written to `logs/<UTC-start>.log` (rotating at ~10MB x5) and to console per `app/logging_config.py`.

Note: `bar_time` is epoch milliseconds (ms) using broker server time.

 

#### WebSocket Metrics (v2)

- The server emits periodic WebSocket metrics for v2 connections.
- Interval: `WS_METRICS_INTERVAL_S` (default 30s).
- Log channel: INFO summary on logger `obs.ws` and DEBUG JSON snapshot.
- Counters:
  - `connections_opened`, `connections_closed`
  - `ok_ticks`, `fail_ticks`, `ticks_items` (sum of items sent in tick lists)
- `ok_indicator_update`, `fail_indicator_update`

#### Indicator Coverage

- Indicators now process for all allowed symbols (defaults to full `RSI_SUPPORTED_SYMBOLS`).
- Timeframes are fixed to the full set: `M1, M5, M15, M30, H1, H4, D1, W1` (no env control).
- Indicator payload coverage: RSI/EMA/MACD/UTBot/Ichimoku (closed bars only).

Observability:
- The indicator scheduler logs per-cycle duration and CPU time: `duration_ms` and `cpu_ms`.
- Structured JSON on `obs.indicator` includes: `{"event":"indicator_poll","pairs_total":n,"processed":m,"errors":k,"duration_ms":t,"cpu_ms":c}`.

Example INFO log line:

```text
📈 ws_metrics | window_s=30 | legacy: conns=0 opened=0 closed=0 ticks_msgs=0 items=0 err=0.000 indicator_msgs=0 err=0.000 | v1: conns=1 opened=1 closed=0 ticks_msgs=120 items=900 err=0.000 indicator_msgs=3 err=0.000 | v2: conns=2 opened=2 closed=0 ticks_msgs=240 items=1800 err=0.004 indicator_msgs=6 err=0.000
```

Notes:
- Counters reset after each report (windowed deltas). Active connection counts are sampled live.
- Low error rates are expected; persistent failures indicate client disconnects or network issues.

### Full API Reference

See `API_DOC.md` for the consolidated WebSocket v2 and REST contracts, examples, and integration guidance.

### REST API Endpoints (complete)

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/health` | GET | Health check and MT5 status | No |
| `/api/indicator` | GET | Latest closed‑bar value(s) for a given indicator across pairs; Currency Strength snapshot | Yes |
| `/api/pricing` | GET | Latest cached price snapshot(s) with daily_change_pct | Yes |
| `/api/symbols` | GET | Symbol search | Yes |
| `/api/news/analysis` | GET | AI-analyzed news data | Yes |
| `/api/news/refresh` | POST | Manual news refresh | Yes |
| `/api/alerts/cache` | GET | In-memory alerts cache (RSI Tracker) | Yes |
| `/api/alerts/by-category` | GET | Alerts grouped by category (type) | Yes |
| `/api/alerts/refresh` | POST | Force refresh alerts cache | Yes |

### RSI Tracker Alert — Closed‑bar Crossing

- Trigger policy: Alerts fire on RSI threshold crossings (Overbought ≥ OB, Oversold ≤ OS) on the current closed bar only (no live/intrabar evaluation).
- Only‑NEW: Not required; detection uses previous vs current closed bar to identify a fresh crossing.
- Startup warm‑up: On first observation per (symbol, timeframe) after server start, the last closed bar is baselined and no email is sent for existing in‑zone conditions; triggers begin from the next new closed bar.
- Rearm policy: Threshold‑level re‑arm. After a trigger at OB, re‑arm when RSI returns below OB; for OS, re‑arm when RSI returns above OS.
- Evaluation timing: Closed‑bar only (evaluates at timeframe boundaries). Intrabar/live evaluation is disabled to ensure RSI‑closed compliance.
- Cooldown: None at pair-level for RSI Tracker; threshold re‑arm only.
Notes:
- Single alert per user from `rsi_tracker_alerts` table.
- Backend enforces closed‑bar evaluation.
- Pairs are fixed in code via `app/constants.py` (no per-alert selection, no env overrides).

### Indicator REST (`/api/indicator`)

Parameters:
- `indicator` (string, required): `rsi` | `quantum` | `currency_strength`
- `timeframe` (string, required): one of `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`
  - Constraint: for `currency_strength`, minimum timeframe is `5M` (requests with `1M` return error `min_timeframe_5M`).
- `pairs` (repeatable or CSV): symbols (1–32). Alias: `symbols`. If omitted, defaults to WS‑allowed symbols (capped to 32).

Response shapes (examples):
```json
{"indicator":"rsi","timeframe":"5M","count":2,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":1696229940000,"value":51.23},{"symbol":"BTCUSDm","timeframe":"5M","ts":1696229940000,"value":48.10}]}
```
```json
{"indicator":"quantum","timeframe":"5M","count":1,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":null,"quantum":{"per_timeframe":{"5M":{"buy_percent":61.5,"sell_percent":38.5,"final_score":23.1,"indicators":{"EMA21":{"signal":"buy","is_new":true,"reason":"Price above EMA"}}}},"overall":{"scalper":{"buy_percent":57.3,"sell_percent":42.7,"final_score":14.6}}}}]}
```

Notes:
- RSI is computed on closed bars only, matching MT5's default RSI(14) close/Wilder. Period is always 14 (requests ignore any other period).
- `times_*` arrays align 1:1 with `rsi[]` and correspond to the closed bars beginning at index `period` in the closed OHLC sequence.
- For exact parity with MT5, request the broker‑suffixed symbol (e.g., `EURUSDm`).

Symbol normalization (canonicalization):
- Input symbols are canonicalized server‑side to prevent common mistakes.
- Rules: trim whitespace, uppercase core instrument (e.g., `eurusd` → `EURUSD`), and force trailing broker suffix to lowercase `m` when present (`EURUSDM` → `EURUSDm`).
- Environment allowlists (e.g., `WS_ALLOWED_SYMBOLS`) and rollout configs are normalized using the same rules.
- Errors like `Unknown symbol: 'EURUSDM'. Similar symbols found: ['EURUSDm']` are automatically avoided; the server now resolves `...M` to `...m`.
 - Alerts: if a pair is configured without the broker suffix (e.g., `BTCUSD`), the evaluators auto‑map it to its broker‑suffixed form (e.g., `BTCUSDm`) when available, ensuring parity with the WebSocket feed and UI.

#### Email Template (RSI)
- Compact, per‑pair card format.
- Fields per card:
  - **pair**: `symbol`
  - **timeframe**: `timeframe`
  - **zone**: derived from `trigger_condition` → `Overbought` or `Oversold`
  - **rsi**: `rsi_value`
  - **price**: `current_price`
  - **ts_local**: local time string (IST by default)
Notes:
- Multiple triggers render multiple cards in a single email.

 

### Global Limit: Max 3 Pairs/User

- The backend now enforces a global cap of 3 unique symbols per user across all active alerts (Heatmap and RSI).
- Enforcement occurs on alert creation endpoints:
  - `POST /api/heatmap-alerts`
  - `POST /api/rsi-alerts`
 
- If adding an alert would exceed the limit, the API returns `400` with a clear message indicating current tracked count and requested additions.
- Tip for UIs: call `GET /api/alerts/user/{user_id}` or the specific per-type list endpoints and compute the union of symbols to show remaining slots.

### RSI Alerts — Closed‑bar Crossing

- Trigger policy: Alerts fire on RSI threshold crossings (Overbought ≥ OB, Oversold ≤ OS) on the current closed bar only (no live/intrabar evaluation).
- Only‑NEW: Not required; detection uses previous vs current closed bar to identify a fresh crossing.
- Startup warm‑up: First observation per key is baselined; no initial trigger for existing in‑zone conditions.
- Rearm policy: Threshold‑level re‑arm. After a trigger at OB, re‑arm when RSI returns below OB; for OS, re‑arm when RSI returns above OS.
- Evaluation timing: Closed‑bar only (evaluates at timeframe boundaries). Intrabar/live evaluation is disabled to ensure RSI‑closed compliance.
- Cooldown: Per (alert, symbol, timeframe, side) cooldown (default 30 minutes). Override with `cooldown_minutes` on the alert (persisted to `rsi_alerts.cooldown_minutes`).
Notes:
- Use `alert_conditions` values `"overbought"`/`"oversold"` to request threshold crossing detection; confirmed triggers return `overbought_cross`/`oversold_cross` in results.
- Current API does not expose `bar_policy` and the backend enforces closed‑bar evaluation.

#### Email Template (RSI)
- Compact, per‑pair card format.
- Fields per card:
  - **pair**: `symbol`
  - **timeframe**: `timeframe`
  - **zone**: derived from `trigger_condition` → `Overbought` or `Oversold`
  - **rsi**: `rsi_value`
  - **price**: `current_price`
  - **ts_local**: local time string (IST by default)
Notes:
- Multiple triggers render multiple cards in a single email.

 

 
- Fields per card:
  - **pair_a/pair_b**: `symbol1`/`symbol2`
  - **rsi_len**: `rsi_period`
  - **timeframe**: `timeframe`
  - **expected_corr**: derived from `trigger_condition` using OB/OS thresholds:
    - positive_mismatch: `one ≥ overbought` and `one ≤ oversold`
    - negative_mismatch: `both ≥ overbought` or `both ≤ oversold`
    - neutral_break: `both between oversold and overbought`
 
  - **trigger_rule**: humanized `trigger_condition`
Notes:
- Multiple triggered pairs render as multiple cards in one email.

 
- Uses a compact, mobile‑friendly HTML card per triggered pair.
- Fields per card:
- **pair_a/pair_b**: Symbols displayed as `ABC/DEF` (e.g., `EUR/USD` vs `GBP/USD`)
  - **lookback**: fixed 50
  - **timeframe**: TF of the evaluation (e.g., `1H`)
  - **expected_corr**: Threshold expression derived from the triggered rule:
 

Notes:
- Multiple triggered pairs render as multiple cards within a single email.
- Subject remains `Trading Alert: <alert_name>` and a text/plain alternative is included.

### Heatmap Alerts — Final Score & Buy Now % (Style‑Weighted)

- Per‑timeframe indicator strength is normalized to a score in [−100..+100].
- Startup warm‑up: For the Tracker, armed state per (alert, symbol) is initialized from current Buy%/Sell% (sides already above thresholds start disarmed) and the first observation is skipped. For the Custom Indicator Tracker, the last signal per (alert, symbol, timeframe, indicator) is baselined and the first observation is skipped.
- Style weighting aggregates across timeframes (matching `app/quantum.py` and the `quantum_update` feed):
  - Scalper: 5M(0.30), 15M(0.30), 30M(0.20), 1H(0.15), 4H(0.05), 1D(0.0)
  - Swing: 30M(0.10), 1H(0.25), 4H(0.35), 1D(0.30)
- Final Score = weighted average of per‑TF scores; Buy Now % = (Final Score + 100)/2.

- Threshold semantics (Tracker):
  - BUY triggers when style‑weighted Buy% crosses up to ≥ `buy_threshold`.
  - SELL triggers when style‑weighted Buy% crosses down to ≤ `sell_threshold` (equivalently, Sell% ≥ `100 − sell_threshold`).
  - Parity: These are the same Buy%/Sell% values sent to the frontend in WebSocket `quantum_update` payloads.

- Detailed evaluation logs:
  - Set `ALERT_VERBOSE_LOGS=true` and `LOG_LEVEL=DEBUG` to enable per‑pair logs:
    - `pair_eval_start` (thresholds and previous armed state)
    - `pair_eval_metrics` (Buy%/Sell%/Final)
    - `pair_eval_criteria` (exact comparisons and re‑arm thresholds)
    - `pair_rearm` (side re‑armed after leaving zone)
    - `pair_eval_decision` (baseline skip or trigger)
    - `heatmap_no_trigger` now includes a `reason` field for clarity

- Backend alignment update:
- The Heatmap/Quantum tracker now reads indicator values from the centralized `indicator_cache` and performs aggregation only. RSI(14), EMA(21/50/200), MACD(12,26,9) are cache-based; UTBot and Ichimoku are computed via `app.indicators` over closed OHLC. New‑signal boosts and quiet‑market damping are applied per spec.
- The Custom Indicator tracker computes real flips for EMA21/EMA50/EMA200 and RSI(14) using cache‑first reads; unknown indicators resolve to neutral.
- Triggers:
  - BUY if Buy Now % ≥ `buy_threshold_min` (and ≤ `buy_threshold_max` when provided)
  - SELL if Buy Now % ≤ `sell_threshold_max` (and ≥ `sell_threshold_min`)
 - Optional Minimum Alignment (N cells): require at least N timeframes to align with the chosen direction (TF strength ≥ buy_min for BUY, ≤ sell_max for SELL).
 - Cooldown: Per (alert, symbol, direction) cooldown window (default 30 minutes). You can override via `cooldown_minutes` on the alert.
- Indicator Flips (Type B): UTBOT, Ichimoku (Tenkan/Kijun), MACD, and EMA(21/50/200) flips supported with Only‑NEW K=3 and 1‑bar confirmation. Optional gate: require style‑weighted Buy Now % ≥ buy_min (BUY) or ≤ sell_max (SELL); defaults 60/40. Cooldown: per (pair, timeframe, indicator) using `cooldown_minutes` (default 30m).

#### Email Template (Custom Indicator Tracker)
- Compact per‑pair card with indicator flip summary.
- Fields per card:
  - **pair**: `symbol`
  - **indicators_csv**: from `alert_config.selected_indicators`
  - **signal**: `trigger_condition` uppercased (`BUY`/`SELL`)
  - **probability**: `buy_percent` for BUY, `sell_percent` for SELL (if available)
  - **timeframe**: `timeframe`
  - **ts_local**: generated server-side in IST
Notes:
- Multiple triggers render multiple cards in one email.
- Subject remains `Trading Alert: <alert_name>` and a text/plain alternative is included.

### Alert Scheduling & Re‑triggering (Global)

- End‑of‑timeframe evaluation only: scheduler runs every 5 minutes; alerts are evaluated on timeframe closes (5M/15M/30M/1H/4H/1D). Tick-driven checks are disabled by default. Note: 1M is supported for market data streaming but alerts are restricted to 5M and higher.
- Crossing/Flip triggers: fire when the metric crosses into the condition from the opposite side (or a regime flip occurs), not on every bar while in‑zone.

See `ALERTS.md` for canonical Supabase table schemas and exact frontend implementation requirements (Heatmap/Indicator/RSI), including field lists, endpoints, validation, and delivery channel setup.
- Re‑arm on exit then re‑cross: once fired, do not re‑fire while the condition persists; re‑arm after leaving the zone and fire again only on a new cross‑in. Changing the configured threshold re‑arms immediately.
- Cooldowns, concurrency, and alert frequency (once/hourly/daily) apply consistently across alert types. Per-user rate limits and digest have been removed.

See `ALERTS.md` for the consolidated alerts product & tech spec.

### Troubleshooting: Only RSI Tracker and Daily emails arrive
- No active alerts: Ensure you have rows in Supabase for `heatmap_tracker_alerts` or `heatmap_indicator_tracker_alerts` with `is_active=true` and non‑empty `pairs` (max 3).
- Thresholds too strict: For Heatmap, start with Buy≥70 / Sell≤30. On higher TFs, RSI may hover mid‑band for long periods.
- Arm/disarm gating (Heatmap): After a trigger, that side disarms and re‑arms as soon as it leaves the zone boundary (no margin). Buy re‑arms when `Buy% < buy_threshold`; Sell re‑arms when `Buy% > sell_threshold`.
 

### 📰 News API Usage (External Source + Internal Endpoints)

#### External Source: ASOasis (Forex Calendar — Today)
- URL (default): `https://api.asoasis.tech/forex-calender/today?timezone=Asia/Kolkata`
- Headers: `client-id: <ASOASIS_API_FOREX_NEWS_CLIENT_ID>`, `client-secret: <ASOASIS_API_FOREX_NEWS_CLIENT_SECRET>`
- Method: GET

Example:
```bash
export ASOASIS_API_FOREX_NEWS_ENDPOINT="https://api.asoasis.tech/forex-calender/today?timezone=Asia/Kolkata"
export ASOASIS_API_FOREX_NEWS_CLIENT_ID="<your_client_id>"
export ASOASIS_API_FOREX_NEWS_CLIENT_SECRET="<your_client_secret>"

curl -s \
  -H "client-id: $ASOASIS_API_FOREX_NEWS_CLIENT_ID" \
  -H "client-secret: $ASOASIS_API_FOREX_NEWS_CLIENT_SECRET" \
  "$ASOASIS_API_FOREX_NEWS_ENDPOINT" | jq .
```

Response shape (example):
```
{
  "count": 15,
  "items": [ { "id": "uuid", "time": 1760039100000, "name": "...", "currency": "USD", "impact": "high", "actual": "", "previous": "", "forecast": "", "revision": "" } ],
  "timezone": "Asia/Kolkata"
}
```

Processing rules:
- Filter: Only `impact == "high"` items (from the source) are analyzed and cached.
- Impact source of truth: Downstream `analysis.impact` mirrors the upstream API `impact` exactly; AI output is ignored for this field.
- Time: `time` may be epoch (ms/seconds) or ISO; normalized to UTC ISO8601 with `Z`.
- Dedup: Prefer upstream `id` as `uuid` for dedup; fallback to `(currency, UTC time, base headline)`.
- Client response hygiene: If any of `actual`, `previous`, `forecast`, `revision` are empty, those fields are omitted in `/api/news/analysis`.

### Example Usage

#### WebSocket Connection (JavaScript)
```javascript
const ws = new WebSocket('ws://localhost:8000/market-v2');

ws.onopen = () => {
  console.log('Connected to broadcast feed');
  // Optional: request a one-time indicators snapshot for a specific key
  // ws.send(JSON.stringify({ action: 'subscribe', symbol: 'EURUSDm', timeframe: '5M', data_types: ['indicators'] }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'ticks') {
    console.log('Ticks:', data.data);
  } else if (data.type === 'indicator_update' || data.type === 'initial_indicators') {
    console.log('Indicators:', data);
  } else if (data.type === 'quantum_update') {
    console.log('Quantum:', data);
  } else if (data.type === 'trending_pairs') {
    console.log('Trending pairs snapshot:', data.data);
  } else {
    console.log('Other:', data);
  }
};
```

#### REST API Request
```bash
# Get historical OHLC data
curl -H "X-API-Key: your_token" \
     "http://localhost:8000/api/indicator?indicator=rsi&timeframe=1H&pairs=EURUSDm&pairs=BTCUSDm"

# Note: Tick data is WebSocket-only. Use `/market-v2` to receive live ticks.

# Trending pairs snapshot
curl -H "X-API-Key: your_token" \
     "http://localhost:8000/trending-pairs"
```

## 🏗️ Architecture Details

### Data Flow

1. **Real-time Data Pipeline**:
   ```
   MT5 Terminal → Data Extraction → Processing → Caching → WebSocket Broadcasting
   ```

2. **News Analysis Pipeline**:
   ```
   External APIs → Merge/Dedup (currency,time,base-headline) → Analyze new/changed → Sort desc → Trim → REST API Serving
   ```

3. **Client Connection Flow**:
   ```
   WebSocket Connection → Authentication → (Optional) Snapshot Request → Data Streaming
   ```

### Caching Strategy

The system uses intelligent caching to optimize performance:

```python
# Global cache structure
global_ohlc_cache = {
    "EURUSD": {
        "5M": deque([100_OHLC_bars]),
        # Only caches subscribed timeframes
    }
}
```

**Benefits**:
- Memory efficient (only caches subscribed data)
- Fast initial data delivery
- Consistent data across clients
- Automatic cleanup of unused caches

### Filesystem-backed News Cache

- **What**: News analysis cache is persisted to disk to survive restarts.
- **Default location**: `news_cache.json` at the project root.
- **Configure**: Set `NEWS_CACHE_FILE` in `.env` to change the path.
- **Lifecycle**:
  - On startup, the scheduler loads existing cache from the file if present.
  - After each successful refresh, the cache and metadata are saved atomically.
  - File format: JSON with `metadata` (timestamps) and `data` (array of news items).

Example `.env`:

```env
NEWS_CACHE_FILE=/var/fxlabs/news_cache.json
```

#### Indicator Cache (Single Source of Truth)

- Closed‑bar indicator values (RSI/EMA/MACD, etc.) are centralized in `app/indicator_cache.py`.
- Each `(symbol, timeframe, params)` keeps a small ring buffer using `deque(maxlen=INDICATOR_RING_SIZE)`.
- All consumers (alerts, WebSocket indicators, debug logs) MUST read from this cache rather than recomputing.

APIs:

```python
from app.indicator_cache import indicator_cache

# Updates (called by indicator pipeline on bar close)
await indicator_cache.update_rsi("EURUSD", "1H", period=14, value=62.1, ts_ms=1695200100000)
await indicator_cache.update_ema("EURUSD", "1H", period=21, value=1.10542, ts_ms=1695200100000)
await indicator_cache.update_macd("EURUSD", "1H", 12, 26, 9, macd_value=0.0012, signal_value=0.0010, hist_value=0.0002)

# Reads (latest)
ts_rsi, rsi = await indicator_cache.get_latest_rsi("EURUSD", "1H", 14) or (None, None)
ts_ema, ema = await indicator_cache.get_latest_ema("EURUSD", "1H", 21) or (None, None)
ts_macd, macd, sig, hist = await indicator_cache.get_latest_macd("EURUSD", "1H", 12, 26, 9) or (None, None, None, None)
```

Configuration:

```env
# Max number of recent indicator values kept per key (default 256)
INDICATOR_RING_SIZE=256
```

Concurrency:

- Access is async‑safe using keyed locks via `app.concurrency.pair_locks` with keys `ind:{symbol}:{timeframe}`.
- Avoid holding other `pair_locks` with the same key simultaneously to prevent deadlocks.

### High-Concurrency Architecture & Scaling

- **ASGI + FastAPI**: Single-process, event-loop concurrency handles many simultaneous REST and WebSocket clients without blocking. Background tasks (news, daily emails, minute alerts, indicator poller) are started via the app lifespan and run concurrently.
- **Broadcast WebSockets (v2)**: A single producer pipeline computes closed-bar indicators every ~10s and broadcasts snapshots to all connected clients. Each client has a paced tick loop (~1 Hz), avoiding per-client heavy work.
- **In-memory caches**: `app/price_cache.py` and `app/indicator_cache.py` serve reads in O(1) with keyed asyncio locks to keep updates consistent under load. REST endpoints read from these caches instead of recomputing.
- **Shaping caps and allowlists**: Environment-driven caps limit work per connection and per request.
  - `ALLOWED_WS_SYMBOLS`, `ALLOWED_WS_TIMEFRAMES`
  - `WS_MAX_SYMBOLS`, `WS_MAX_TFS_PER_SYMBOL`, `WS_MAX_SUBSCRIPTIONS`
  - REST endpoints cap symbols to 32 and validate timeframes.
- **Backpressure/timeouts**:
  - WebSocket send loops pace at ~1s intervals and use best-effort non-blocking sends.
  - External calls (e.g., Supabase via `AlertCache`) use strict client timeouts.
- **Security & isolation**: `X-API-Key` required for REST; WebSocket mirrors REST auth optionally. CORS origins are configured per deployment. Multi-tenancy uses separate entry points (`fxlabs-server.py` / `hextech-server.py`) with tenant-scoped credentials and branding.
- **Observability**: Periodic WebSocket metrics and per-update indicator logs track throughput, failures, and latency without affecting hot paths.

Operational guidance:
- Start with conservative allowlists and caps; increase gradually while monitoring CPU, memory, and send loop latencies.
- For higher fan-out, scale vertically (CPU/RAM) first. If horizontally scaling processes, ensure only one instance performs heavy indicator polling per market feed or stagger instances by symbol/timeframe to avoid duplicate work.

### Performance Characteristics

| Metric | Before Optimization | After Optimization | Improvement |
|--------|-------------------|-------------------|-------------|
| Memory Usage | ~30MB (1000 users) | ~5MB (1000 users) | 83% reduction |
| CPU Utilization | ~11 cores at 100% | <10% utilization | 95% reduction |
| Bandwidth | ~7.6 GB/second | <1 Mbps | 99.9% reduction |
| Infrastructure Cost | $8K-20K/month | <$200/month | 95% reduction

## 🧪 Testing

### Run Tests
```bash
# Test WebSocket connections
python test_websocket_client.py

# Test REST API
curl "http://localhost:8000/health"
```

### Indicators: Unit Checks and Micro-Benchmark

- Prerequisite: MT5 terminal is installed and accessible. Optionally set `MT5_TERMINAL_PATH` in your environment.

Run closed-bar indicator unit checks (RSI/EMA/MACD/UTBot/Ichimoku) over a small set of symbols×timeframes:

```bash
python tests/test_indicators.py
```

Run a small micro-benchmark of indicator computations (latest closed bar) for 3–5 symbols across `5M/1H/1D`:

```bash
python tests/bench_indicators.py
```

### Indicators: Parity Checks (Closed‑Bar)

- Prerequisite: MT5 terminal is installed and accessible. Optionally set `MT5_TERMINAL_PATH` in your environment.

Run parity checks across the last N closed bars for 3–5 symbols and multiple timeframes. Enforces tolerances from `REARCHITECTING.md`:

```bash
python tests/test_parity.py
```

Tolerances:
- RSI (Wilder): abs diff ≤ 0.15
- EMA(21/50/200): tail abs diff ≤ 1e‑9 (identical math)
- MACD(12,26,9) histogram: abs diff ≤ 5e‑4
- Daily % change (Bid): parity within ≤ 0.10%

Notes:
- These tests require a live MT5 connection and will skip gracefully if MT5 is unavailable.
- Parity tolerances follow `REARCHITECTING.md` (e.g., RSI ≤ 0.15 abs diff; MACD hist ≤ 5e-4).

### Test HTML Client
Open `test_websocket.html` in your browser to test WebSocket connections interactively.

## 🚀 Deployment

### Production Deployment

The system is configured for production deployment with Cloudflare Tunnel:

```yaml
# config.yml
tunnel: 5612346e-ee13-4f7b-8a04-9215b63b14d3
ingress:
  - hostname: api.fxlabsprime.com
    service: http://127.0.0.1:8000
```

### Scaling Recommendations

- **100-500 users**: Single server (4GB RAM, 2 CPU cores)
- **500-1000 users**: Add Redis caching (8GB RAM, 4 CPU cores)
- **1000+ users**: Multi-server deployment with load balancer

## 🔧 Development

### Project Structure (Modular)
```
Fxlabs.ai_Back_end/
├── app/
│   ├── __init__.py                    # App package
│   ├── config.py                      # Env-backed configuration (no functional change)
│   ├── models.py                      # Pydantic models and enums
│   ├── mt5_utils.py                   # MT5 helpers, OHLC cache, timeframe logic
│   ├── news.py                        # News fetching, AI analysis, scheduler, cache
│   ├── alert_cache.py                 # Alert configuration cache management
│   ├── email_service.py               # SendGrid email service for alerts
│   ├── heatmap_alert_service.py       # Heatmap alert processing
│   ├── rsi_alert_service.py           # RSI alert processing
 
│   └── indicators.py                  # Centralized indicator math (RSI/EMA/MACD/Ichimoku/UT Bot)
├── server.py                          # FastAPI app, routes & websockets (imports from app/*)
├── requirements.txt                   # Python dependencies
├── config.yml                         # Cloudflare tunnel config
├── config.env.example                 # Environment variables template
├── test_websocket_client.py           # WebSocket test client
├── test_websocket.html                # HTML test client
├── generate_alert_backup.py           # Alert data backup utility
├── alert_data_backup.txt              # Alert configuration backup
├── alert_system_test_results_*.txt    # Comprehensive test results
└── README.md                          # This file
```

The modular structure isolates responsibilities while preserving all existing behavior and endpoints. Environment variable names and usage remain unchanged.

### Console Logging Timestamps
- All console prints now include an ISO‑8601 timestamp (local time with timezone), automatically applied via a top‑level `sitecustomize.py`.
- No code changes are needed in modules: any `print(...)` will appear as `[2025-09-25T12:34:56+05:30] ...` in stdout/stderr.
- Behavior is idempotent and safe: the patch avoids double‑wrapping `print`.
- If you need to opt out for any reason (e.g., ad-hoc debugging), temporarily comment or remove `sitecustomize.py` from the project root in your working copy.

### Key Dependencies
- **FastAPI**: Web framework with async support
- **MetaTrader5**: MT5 Python API integration
- **WebSockets**: Real-time communication
- **Pydantic**: Data validation and serialization
- **aiohttp**: Async HTTP client for external APIs
- **SendGrid**: Email service for alert notifications
- **Supabase**: Database for alert configurations

### MT5 Integration
- Full MT5 integration, data fetch, WebSocket streaming, alerts math, and live RSI debugging are documented in `MT5.md`.
- Single shared MT5 session with unified OHLC helpers/caching in `app/mt5_utils.py` (no duplication).
- WebSocket endpoint: `/market-v2` (broadcast-only). See `test_websocket_client.py` for usage.

### New Helpers

- `app/mt5_utils.py:get_current_tick(symbol: str) -> Optional[Tick]`
  - Ensures the symbol is selected and returns a `Tick` from `mt5.symbol_info_tick`.
  - Used by alert services; system now requires real MT5 data only (no simulation).

## 📊 Supported Data Types

### Timeframes
- **1M** - 1 Minute (streaming/data only; alerts enforce 5M minimum)
- **5M** - 5 Minutes
- **15M** - 15 Minutes
- **30M** - 30 Minutes
- **1H** - 1 Hour
- **4H** - 4 Hours
- **1D** - 1 Day
- **1W** - 1 Week

### Data Models
- **Tick Data**: Real-time price updates (bid prices only sent to frontend)
- **OHLC Data**: Candlestick data (open, high, low, close, volume)
- **News Data**: Economic news with AI analysis
- **Analysis Data**: Trading impact assessment

### Response Shape: `/api/news/analysis`

Each item in `data` contains:

```json
{
  "headline": "Employment Change (Data Not Loaded) - Data Not Loaded",
  "forecast": "21.2",
  "previous": "24.5",
  "actual": "0.0",
  "currency": "AUD",
  "time": "2025-09-18T01:30:00Z",
  "analysis": {
    "effect": "bearish",
    "impact": "high",
    "full_analysis": "... concise AI explanation ..."
  },
  "analyzed_at": "2025-09-17T12:06:06.425760+00:00"
}
```

Notes:
- `analysis.effect`: bullish | bearish | neutral (lowercase)
- `analysis.impact`: high | medium | low (lowercase), mirroring the upstream API `impact` value; AI predictions for impact are ignored.
- Removed fields: `currencies_impacted`, `currency_pairs`

Model behavior:
- The AI analysis provides only the directional bias (`effect`) and a concise explanation. Impact used in responses mirrors the source API and is not taken from AI output. Full prompt used:
  ```
  You are a Forex macro event classifier used BEFORE an economic release. Output exactly:
  {
    "effect": "bullish|bearish|neutral",
    "explanation": "<max 2 sentences>"
  }
  Constraints:
  - Lowercase enums only.
  - No extra fields or text.
  - Do NOT include any field named 'impact' in your response.

  INPUT
  Currency: {news_item.currency}
  News: {news_item.headline}
  Time: {news_item.time or 'N/A'}
  Forecast: {news_item.forecast or 'N/A'}
  Previous: {news_item.previous or 'N/A'}
  Source impact hint: {news_item.impact or 'N/A'}

  A) CONTEXT (impact is provided by API; do not output it)
  1) Consider standard taxonomy only to reason about magnitude in the explanation; DO NOT output an 'impact' field.
  2) Taxonomy by EVENT FAMILY (based on what historically moves FX):
     TIER-1 (default "high"):
     - CPI (headline/core), PCE (US), central-bank rate decisions/statements/pressers/minutes, major labor (NFP/Employment Change, Unemployment Rate, Average/Hourly Earnings), GDP "advance/flash", ISM PMIs (US), Flash PMIs (EZ/UK), Retail Sales (US/UK/CA headline; US control group). 
     TIER-2 (default "medium"):
     - PPI, GDP "second/final", Retail Sales ex-autos (non-US), durable goods (ex-transport), trade balance, housing starts/building permits, consumer/business confidence, final PMIs.
     TIER-3 (default "low"):
     - Regional/small surveys, auctions, secondary indices with limited FX pass-through.
  3) Currency-bloc adjustment:
     - For G10 (USD, EUR, JPY, GBP, AUD, NZD, CAD, CHF, SEK, NOK): keep tiers as above.
     - For non-G10/minor economies: downgrade one tier unless the pair is commonly traded against USD/EUR and the event is TIER-1.
  4) Do NOT upgrade events due to hype or proximity in time. Color codes indicate tiers but don't override taxonomy.

  B) EFFECT (directional bias for the LISTED currency, pre-release)
  5) Do NOT guess the actual. Infer bias from forecast vs prior, policy stance, and trend (inflation/labor/growth/central-bank guidance).
  6) If genuinely mixed/flat, set effect="neutral". Direction refers to the listed currency, not the pair.

  C) DATA HYGIENE (pre-release)
  8) You may look up consensus/stance from reliable sources. Do NOT treat previews as actuals.
  9) EXPLANATION ≤2 sentences: (i) impact tier rationale, (ii) bias rationale. No filler.
  ```
- Parsing first attempts to load the JSON; if unavailable, it falls back to regex to extract `effect`/`explanation`. The final `full_analysis` is the explanation text (never raw JSON).
- `analysis.impact` mirrors the upstream API `impact` field exclusively; AI output never overrides it.

## 🔒 Security Features

- **API Token Authentication**: Required for all REST endpoints
- **CORS Configuration**: Configurable cross-origin resource sharing
- **Input Validation**: Pydantic models for data validation
- **Error Sanitization**: Safe error messages without sensitive data
- **Rate Limiting**: Built-in protection against abuse

## ✅ Known Issues and Notes

- High severity:
  - NumPy 2.x compatibility: MetaTrader5 5.0.45 is built against NumPy 1.x and fails to import with NumPy 2.x on some environments (Windows). The project pins `numpy<2` in `requirements.txt`. If you already have NumPy 2.x installed, downgrade within your venv:
    - PowerShell: `pip uninstall -y numpy; pip install "numpy<2"`
    - Then reinstall MT5 if needed: `pip install --force-reinstall --no-cache-dir MetaTrader5==5.0.45`

- Medium severity:
  - External API keys (Perplexity/ASOasis) are expected via env; missing keys will limit news analysis.
  - News analyzer uses simple keyword extraction to derive effect; this is heuristic, as before.
  - Email per-user rate limiting and digest have been removed. Alerts are sent immediately when not blocked by the value-based cooldown.
  - Closed-bar gating for alert evaluation is tracked per alert/user (not globally by symbol/timeframe). This ensures multiple users with identical configurations are each evaluated every cycle.

- Low severity:
  - CORS defaults to allow-all when `ALLOWED_ORIGINS` is empty (dev-friendly, same as before). For production, set explicit origins.
  - Tests are minimal; add unit tests per module in future iterations.

## 📈 Monitoring & Health Checks

### Health Endpoint
```bash
curl http://localhost:8000/health
```

Returns:
```json
{
  "status": "ok",
  "mt5_version": "5.0.45"
}
```

### Logging
All logs include timestamps with timezone offset using the format:
`YYYY-MM-DD HH:MM:SS±ZZZZ | LEVEL | module | message`.

You can control verbosity via `LOG_LEVEL` (default `INFO`).

#### Verbosity Flags (non-critical logs)
- `LIVE_RSI_DEBUGGING` — emits periodic closed‑bar RSI for BTC/USD 5M (default `false`).
- `LOG_ENV_DUMP` — prints full environment snapshot at startup (default `false`; may include secrets).
- `ALERT_VERBOSE_LOGS` — enables non‑critical alert/daily diagnostics like config echoes and no‑trigger reasons (default `false`).
- `NEWS_VERBOSE_LOGS` — enables verbose news fetch/parse/update prints (default `false`).
- `BYPASS_EMAIL_ALERTS` — bypasses all email alerts and logs when alerts are bypassed (default `false`).

Examples:
```bash
export LIVE_RSI_DEBUGGING=true
export ALERT_VERBOSE_LOGS=true
# Bypass all email alerts for testing:
export BYPASS_EMAIL_ALERTS=true
# Keep sensitive env quiet by default:
export LOG_ENV_DUMP=false
```

The system provides comprehensive logging for:
- Connection events
- Data processing errors
- API request/response cycles
- Performance metrics

#### Indicator Poller Observability (OBS-1)
- Human-readable cycle summary at INFO:
  - `🧮 indicator_poll | pairs=<n> processed=<m> errors=<k> duration_ms=<t>`
- Structured JSON logs at DEBUG via logger `obs.indicator`:
  - Per-item update (one log per processed bar):
    ```json
    {"event":"indicator_item","sym":"EURUSDm","tf":"5M","bar_time":1696229940000,"latency_ms":85,"rsi14":51.23,"ema":{"21":1.06871,"50":1.06855,"200":1.06780},"macd":{"macd":0.00012,"signal":0.00010,"hist":0.00002}}
    ```
  - Per-cycle metrics:
    ```json
    {"event":"indicator_poll","pairs_total":49,"processed":39,"errors":0,"duration_ms":134}
    ```
- Notes:
  - JSON logs are emitted at DEBUG to avoid INFO spam. Set `LOG_LEVEL=DEBUG` to enable.
  - Observability errors never break scheduling; metrics/logging are best-effort only.

- #### Live RSI Debugging cadence (5M closed‑bar, cache‑aligned)
- `🧭 liveRSI` logs are emitted directly from the indicator scheduler when it detects a new closed `5M` bar for `BTCUSDm` only. Values are sourced from the same indicator pipeline and cache used by alerts and WebSocket indicator streaming (single source of truth).
- When `LIVE_RSI_DEBUGGING=true`, logs appear shortly after each 5M close (sub‑200 ms latency target).
- Previous helper `app.mt5_utils._maybe_log_live_rsi()` and the dedicated boundary task have been removed to prevent duplicate math and drift. The gating is implemented in `server.py`.

#### Alert Evaluation Cadence (Closed‑Bar)
- The alert evaluator loop sleeps until the next `5M` boundary and runs immediately after it. This ensures RSI‑closed and other closed‑bar math are computed right after the candle closes (no drift). Higher timeframes (15M/30M/1H/4H/1D/W1) are also aligned since their boundaries are multiples of 5 minutes.

#### Troubleshooting: WebSocket "accept" error
- Symptom: RuntimeError "WebSocket is not connected. Need to call 'accept' first." in logs.
- Meaning: The client closed or the connection wasn't fully established when the server tried to read. This is a normal transient condition with flaky clients or quick reconnects.
- Handling: The server now treats this as a clean disconnect and exits the read loop gracefully; no action required unless it's frequent. If frequent, check client networking and retry logic.

#### File Logging (added)
- Logs are written both to the terminal and to `logs/<YYYY-MM-DDTHH-mm-ssZ>.log` (UTC start time) in the repository root. A new file is created for each server start.
- File logs rotate automatically at ~10 MB per file with up to 5 backups kept: `<timestamp>.log`, `<timestamp>.log.1`, ..., `<timestamp>.log.5`.
- The `logs/` directory is created automatically on startup.

Optional environment overrides:
- `LOG_DIR` — change the directory for log files (default: `<repo>/logs`).
- `LOG_MAX_BYTES` — max size of a single log file in bytes (default: `10485760`).
- `LOG_BACKUP_COUNT` — number of rotated backups to keep (default: `5`).

#### Detailed Evaluation Logs (per alert, per symbol/pair)
At DEBUG level, evaluators emit concise reasons when a trigger does not fire, so you can see exactly how each alert was evaluated:

- RSI Tracker
  - `rsi_insufficient_data` — fewer than 2 RSI points available
  - `rsi_rearm_overbought` / `rsi_rearm_oversold` — armed state toggled after exiting threshold
  - `rsi_no_trigger` — includes reason (`no_cross | disarmed_overbought | disarmed_oversold | already_overbought | already_oversold | within_neutral_band`) and values (`prev_rsi`, `curr_rsi`, thresholds, armed flags)
 
  - `corr_no_mismatch` — computed condition did not indicate mismatch; includes `label` and `value`
  - `corr_persisting_mismatch` — mismatch persisted from previous bar (no new trigger)
- Heatmap Tracker
  - `heatmap_eval` — Buy%/Sell%/Final Score for each symbol
  - `heatmap_no_trigger` — includes Buy%/Sell%, thresholds, and armed flags when no trigger
- Indicator Tracker
  - `indicator_signal` — current and previous signal
  - `indicator_no_trigger` — includes reason (`neutral_signal | no_flip`) when no trigger occurs

At INFO level, the scheduler emits compact batch summaries after each evaluation cycle:
- `rsi_tracker_eval | triggers: <n>`
 
- `heatmap_tracker_eval | triggers: <n>`
- `indicator_tracker_eval | triggers: <n>`

#### Human‑Readable Emoji Logging (v2.2.0)
Alert evaluations and actions are now logged in a clean, human‑readable format with emojis using `app/alert_logging.py`.

Key events (examples):
- `🎯 rsi_tracker_triggers | alert_id: abc123 | count: 3`
- `📤 email_queue | alert_type: rsi | alert_id: abc123`
- `⚠️ market_data_stale | symbol: EURUSD | age_minutes: 12`
  

Notes:
- Complex objects (lists/dicts) are not dumped; shown as `…` to avoid noisy logs and accidental payload leaks.
- Logs are optimized for humans in terminals, not JSON processors.
- Email send failures no longer log raw provider response bodies.
- Third‑party verbose clients (e.g., SendGrid `python_http_client`) are set to WARNING to suppress payload dumps. To see them again, manually set `logging.getLogger("python_http_client").setLevel(logging.DEBUG)` in your session.

Modules instrumented: `rsi_alert_service`, `rsi_tracker_alert_service`, `heatmap_tracker_alert_service`, `heatmap_indicator_tracker_alert_service`, `alert_cache`, and `email_service` (queue/send summaries).

To enable DEBUG‑level detailed evaluations, set:
```bash
export LOG_LEVEL=DEBUG
```

#### Logging Optimization (v2.0.1)
**Problem Fixed**: Alert services were logging extensively on every tick, even when no alerts were triggered, causing massive log spam.

**Solution Implemented**:
- **Reduced INFO-level spam**: Changed verbose processing logs from INFO to DEBUG level
- **Conditional logging**: Only log alert check summaries when there are active alerts or triggers
- **Smart alert checking**: Skip alert processing entirely when no active alerts exist
- **Preserved essential logs**: Alert triggers, errors, and email notifications remain at INFO level

**Logging Levels**:
- **INFO**: Alert triggers, email notifications, errors, and summaries (when alerts exist)
- **DEBUG**: Detailed processing steps, data retrieval, and calculations
- **WARNING**: Missing data, fallback scenarios, and configuration issues
- **ERROR**: Critical failures and exceptions

**Performance Impact**:
- **Log volume reduction**: ~95% reduction in log output during normal operation
- **CPU efficiency**: Eliminated unnecessary string formatting and I/O operations
- **Server stability**: Reduced log spam prevents disk space issues and improves performance

**Specific Changes Made**:
- **Heatmap Alert Service**: Changed "Check Complete: X alerts processed, 0 triggered" from INFO to DEBUG level
- **RSI Alert Service**: Changed "Check Complete: X alerts processed, 0 triggered" from INFO to DEBUG level  
 
- **Conditional Logic**: Only log at INFO level when alerts are actually triggered, reducing terminal noise by 95%

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🧠 Smart Email Alert Cooldown System

### Overview
The email service includes an intelligent value-based cooldown mechanism that prevents spam emails while allowing significant market movements to trigger alerts.

### How It Works
- **10-minute base cooldown**: Once an alert email is sent, similar alerts are blocked for 10 minutes
- **Value-based intelligence**: RSI values within 5 points are considered similar and trigger cooldown
- **Smart breakthrough**: If RSI changes by 5+ points, the alert breaks through cooldown and is sent
- **Automatic cleanup**: Old cooldown entries are automatically cleaned up after 24 hours

### Example Scenarios
```
✅ SMART COOLDOWN EXAMPLES:

1. RSI 70.1 for EURUSD → Email sent ✅
2. RSI 70.2 for EURUSD → Email blocked (cooldown, <5 point diff) 🕐
3. RSI 70.5 for EURUSD → Email blocked (cooldown, <5 point diff) 🕐
4. RSI 75.1 for EURUSD → Email sent ✅ (5+ point difference breaks cooldown)
5. RSI 30.1 for EURUSD → Email sent ✅ (oversold, completely different)

❌ OLD SYSTEM PROBLEMS (FIXED):
- RSI 70.1 → 80.1 → 30.1 all blocked (same "overbought" condition)
- User missed strong signals and oversold opportunities
```

### Benefits
- **Prevents spam**: No more multiple emails for similar RSI values
- **Allows important signals**: Significant RSI changes (5+ points) break through cooldown
- **Market-aware**: Understands that RSI 70 vs 80 vs 30 are different trading signals
- **User-friendly**: Users get meaningful alerts without email fatigue
- **Efficient**: Reduces email costs while maintaining alert quality

### Configuration
```python
self.cooldown_minutes = 10      # Base cooldown period
self.rsi_threshold = 5.0        # RSI difference threshold for breakthrough
```

### Technical Details
- **Multi-alert support**: Works with RSI and Heatmap alerts
- **Smart value extraction**: Handles different data structures for each alert type
- **Hash generation**: Includes actual values (RSI, strength) rounded to 1 decimal
- **Value comparison**: Compares current vs last sent values for breakthrough detection
- **Breakthrough logic**: If any value difference ≥ threshold, alert is sent
- **Memory management**: Automatic cleanup prevents memory leaks

### Alert Type Support
- **RSI Alerts**: Tracks `rsi` values (e.g., 70.1 → 75.1 = breakthrough)
- **Heatmap Alerts**: Tracks `strength` values and RSI from indicators
 

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Check the troubleshooting section in README_OHLC.md
- Review the test files for usage examples

---

**Version**: 2.0.0  
**Last Updated**: September 2025  
**Compatibility**: Python 3.8+, MT5 Python API, FastAPI 0.100+

## 🛠️ Troubleshooting
### SyntaxError at server.py:808 "try:" on startup (Windows)
- Symptom: Startup fails with a traceback pointing to `server.py` around line ~808 with `try:` highlighted.
- Cause: A nested `try` block was placed inside an `except` in the indicator scheduler, which could lead to parser confusion and brittle indentation handling in some environments.
 
- Verify: `python fxlabs-server.py` starts cleanly; `/health` returns `{"status":"ok", ...}`.
### Ctrl+C hangs at "Waiting for application shutdown"
- Symptom: After pressing Ctrl+C, logs show `INFO:     Shutting down`, `connection closed` lines, and then `INFO:     Waiting for application shutdown.` without exiting.
- Cause: Background schedulers (e.g., indicators/news) must be cancelled so the lifespan shutdown can complete. If any long-running task isn't cancelled, the server waits indefinitely.
- Fix in code: The shutdown sequence cancels all background tasks. Ensure you are on the latest code where `server.py` cancels scheduler tasks with proper `CancelledError` handling.
- Tip: If you still see a hang, check for any custom added loops without `CancelledError` handling. All loops should `await asyncio.sleep(...)` and properly handle `asyncio.CancelledError`.

### "RSI Tracker: triggers exist in DB but no emails/logs"
- Note: DB trigger tables have been removed. Use INFO/DEBUG logs and email diagnostics instead.
- **Quick checks**:
  - **notification_methods**: Ensure your alert has `"notification_methods": ["email"]`. If it's `"browser"` only, emails won't send.
    - Supabase check example: verify the `notification_methods` column for your alert row includes `"email"`.
  - **Email service configured**: Set `SENDGRID_API_KEY`, `FROM_EMAIL`, `FROM_NAME` in `.env`. The service logs diagnostics if not configured.
  - **Log level**: Set `LOG_LEVEL=INFO` (or `DEBUG`) so you see tracker/email logs.
  - **Cooldown only**: Emails are suppressed for 10 minutes for similar values via smart cooldown. No per-user rate limits or digest.
  - **Scheduler running**: The minute scheduler runs inside `server.py` lifespan; confirm the server is started normally (not as a one-off script).
  - **Supabase creds**: `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` must be set for cache/trigger logging to work.
  
Expected logs when working:
  - `🚨 RSI Tracker triggers detected: ...`
  - `📤 Queueing email send for RSI Tracker ...`
  - Email service logs like `📧 RSI Alert Email Service - Starting email process` and `📊 SendGrid response: Status 202`.

 

### "SendGrid not configured, skipping RSI alert email"
- Cause: `EmailService` didn't initialize a SendGrid client (`self.sg is None`). This happens when either the SendGrid library isn't installed or tenant-specific credentials are missing.
- Fix quickly:
  - Install deps in your venv: `pip install -r requirements.txt` (includes `sendgrid`)
  - Provide tenant-specific credentials via environment or `.env` (auto-loaded now):
    - FxLabs Prime: `FXLABS_SENDGRID_API_KEY=...`, `FXLABS_FROM_EMAIL=verified@yourdomain.com`, `FXLABS_FROM_NAME=FxLabs Prime Alerts`
    - HexTech: `HEXTECH_SENDGRID_API_KEY=...`, `HEXTECH_FROM_EMAIL=verified@yourdomain.com`, `HEXTECH_FROM_NAME=HexTech Alerts`
  - Ensure your process actually sees the variables:
    - macOS/Linux: `.env` is auto-loaded; no manual `export` needed
    - Windows: `start.ps1`/`start.bat` also load `.env`
  - Verify your SendGrid sender: Single Sender verification or Domain Authentication, otherwise SendGrid returns 400/403 and emails won't send.
- Where to set: copy `config.env.example` to `.env` and fill values, or set env vars directly in your deployment.

### "HTTP Error 403: Forbidden" during send (intermittent)
- Symptom: Logs show `❌ Error sending ... email: HTTP Error 403: Forbidden` while other emails sometimes succeed.
- Most common root causes:
  - Sender identity mismatch: `FROM_EMAIL` is not a verified Single Sender or part of an authenticated domain. If only `alerts@fxlabsprime.com` is verified, sending from `alerts@alerts.fxlabsprime.com` will 403. The code requires tenant-specific `FROM_EMAIL`; no default is used.
  - API key scope too narrow: The `SENDGRID_API_KEY` lacks the `Mail Send` permission. Regenerate with Full Access or include `Mail Send`.
  - IP Access Management: If enabled in SendGrid, requests from non-whitelisted IPs are blocked with 403. Whitelist the server IP(s).
  - Region mismatch: EU-only accounts must use the EU endpoint; ensure your environment uses the correct SendGrid region (contact SendGrid if unsure).
- Why intermittent? Different processes or shells might pick up different env files. Ensure you set the tenant-specific variables (`FXLABS_*` for FxLabs Prime or `HEXTECH_*` for HexTech) in the active environment for that process. No code defaults are used.
- What we log now (for failures): status, a trimmed response body, masked API key, and `from/to` addresses to speed up diagnosis without leaking secrets.
- Quick checklist:
  - Confirm your env defines tenant-specific keys: for FxLabs Prime use `FXLABS_SENDGRID_API_KEY`, `FXLABS_FROM_EMAIL`, `FXLABS_FROM_NAME`; for HexTech use `HEXTECH_SENDGRID_API_KEY`, `HEXTECH_FROM_EMAIL`, `HEXTECH_FROM_NAME`.
  - Verify the sender identity in SendGrid (Single Sender) or authenticate the `fxlabsprime.com` domain.
  - If you use IP Access Management, add the server IP.
  - In SendGrid → API Keys, confirm the key includes `Mail Send`.
  - Run `python send_test_email.py you@example.com` (in an environment where running is allowed) to verify the path end-to-end.

#### Email Configuration Diagnostics (enhanced logs)
When email sending is disabled, the service now emits structured diagnostics showing what's missing or invalid (without leaking secrets). Example:

```
⚠️ Email service not configured — RSI alert email
   1) sendgrid library not installed (pip install sendgrid)
   2) Tenant API key missing (set FXLABS_SENDGRID_API_KEY or HEXTECH_SENDGRID_API_KEY)
   Values (masked): SENDGRID_API_KEY=SG.************abcd, FROM_EMAIL=alerts@fxlabsprime.com, FROM_NAME=FxLabs Prime
   Hint: configure tenant-specific email credentials (FXLABS_SENDGRID_API_KEY/FXLABS_FROM_EMAIL/FXLABS_FROM_NAME or HEXTECH_*) — no global defaults
```

Notes:
- API key is masked (prefix + last 4 chars) for safety.
- If the key doesn't start with `SG.`, a hint is logged to double‑check the value.
- `rsi_alert_service` will also surface a one‑line summary under "Email diagnostics:".

### "AttributeError: 'RSIAlertService' object has no attribute '_allow_by_pair_cooldown'"
- Cause: Older builds missed the per (alert, symbol, timeframe, side) cooldown helper in `app/rsi_alert_service.py` while it was referenced during RSI checks.
- Status: Fixed by adding `_allow_by_pair_cooldown(...)` and enforcing the documented RSI cooldown. Ensure your local tree includes this method.
- What to check: Open `app/rsi_alert_service.py:1` and verify `_allow_by_pair_cooldown(self, alert, alert_id, symbol, timeframe, side)` exists and uses `cooldown_minutes` (default 30).

### "ModuleNotFoundError: No module named 'sendgrid'"
- Ensure dependencies are installed inside your virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```
- Set SendGrid credentials in `.env` (tenant-specific only):
```env
# For FxLabs Prime
FXLABS_SENDGRID_API_KEY=your_sendgrid_api_key
FXLABS_FROM_EMAIL=alerts@fxlabsprime.com
FXLABS_FROM_NAME=FxLabs Prime Alerts

# For HexTech
# HEXTECH_SENDGRID_API_KEY=your_sendgrid_api_key
# HEXTECH_FROM_EMAIL=alerts@hextech.ae
# HEXTECH_FROM_NAME=HexTech Alerts
```
- Behavior without SendGrid: The server will start and log a warning; email sending is disabled but other features work.

### Pydantic v2 Config Warning
If you saw:
```
UserWarning: Valid config keys have changed in V2: 'allow_population_by_field_name' → 'populate_by_name'
```
This is resolved by migrating models to Pydantic v2 `model_config` with `populate_by_name=True` (already updated in `app/models.py`). No action required on your part.

### Windows Global Python vs venv
If you still get missing modules on Windows, confirm you're running inside the venv:
```powershell
$env:VIRTUAL_ENV
python -c "import sys; print(sys.executable)"
```
It should point to your project's `.venv` path. If not, re-run activation and reinstall requirements.

### "Delivered in SendGrid, but no email in inbox"
- Check Spam/Junk, Promotions/Updates/All Mail, and any mailbox filters that might skip the inbox or auto-archive.
- Verify SendGrid Email Activity: ensure there is a "delivered" event (250 OK). Open the event to view the SMTP response and message identifiers.
- Confirm the recipient mailbox actually exists: send a plain-text test from another account to the same address (e.g., `test@asoasis.tech`) and see if it bounces.
- If using Google Workspace: in Admin Console, use Email Log Search for the recipient/time or Message-ID to see if it was quarantined, routed, or marked spam; release if needed.
- If using Cloudflare Email Routing or any forwarder: verify the route exists, forwarding target is valid, and check routing logs for acceptance/drops.
- Authenticate your From domain in SendGrid:
  - Complete Domain Authentication (CNAMEs) and send from an aligned subdomain (e.g., `alerts@alerts.fxlabsprime.com`).
  - Ensure SPF includes `include:sendgrid.net`, DKIM passes, and set DMARC to `p=none` during testing; move to `quarantine`/`reject` after validation.
- Reduce spam likelihood: include both `text/plain` and `text/html` parts, avoid URL shorteners, keep images minimal, and use a consistent `FROM_EMAIL` that matches your authenticated domain.
- Check suppression lists anyway: make sure the recipient isn't present under Bounces/Blocks/Spam Reports; remove if found, then resend.
- Confirm SendGrid Sandbox Mode is OFF under Mail Settings. Sandbox disables actual delivery even if the API returns 2xx.
- A/B test: send the same message to a known Gmail/Outlook inbox to isolate whether the issue is at the sender or recipient domain.
- Optional (code): call `_add_transactional_headers(mail)` before sending to add transactional headers like `List-Unsubscribe` and a category, which can improve inboxing.

What to collect for escalation: UTC timestamp, recipient, subject, SendGrid Message ID/X-Message-Id, SMTP 250 response line, and the receiving MTA hostname (e.g., `gmail-smtp-in.l.google.com`).

### Code-Side Deliverability Hardening
- Dual-part emails: The backend now sends both `text/plain` and `text/html` bodies for all alert emails. Many receivers score plain-text positively.
- Transactional headers: Adds `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, a consistent `X-Mailer`, and a category header to help mailbox classification. When `PUBLIC_BASE_URL` and `UNSUBSCRIBE_SECRET` are set, a one-click HTTP List-Unsubscribe URL is added alongside the mailto link.
- Disable tracking: Click- and open-tracking are disabled on alert emails to avoid link rewriting and tracking pixels that can push messages to Promotions/Spam.
- Unsubscribe persistence: Users who click the one‑click unsubscribe are stored in `UNSUBSCRIBE_STORE_FILE`; all future sends to them are skipped.
- Stable reference ID: Adds an `X-Entity-Ref-ID` derived from the alert to aid thread detection and support.
- Consistent Reply-To: Sets `Reply-To` to the sender for consistent header presence.

Operational notes:
- Keep `FROM_EMAIL` on your authenticated domain/subdomain (e.g., alerts@alerts.fxlabsprime.com).
- Avoid URL shorteners and excessive links in alert content.
- DMARC alignment: after verifying inboxing, move DMARC policy from `p=none` to `quarantine`/`reject` gradually.

### Outlook/Office 365: "We can't verify this email came from the sender"
This is a DMARC alignment/authentication issue. Fix by authenticating the domain and aligning the From address.

Checklist:
- Domain Authentication in SendGrid: Settings → Sender Authentication → Domain Authentication. Choose a dedicated subdomain (e.g., `alerts.fxlabsprime.com`).
  - Add the DKIM CNAMEs SendGrid provides (typically `s1._domainkey.alerts.fxlabsprime.com` and `s2._domainkey.alerts.fxlabsprime.com`).
  - Enable "Custom Return Path" (bounce domain), e.g., `em.alerts.fxlabsprime.com` CNAME to SendGrid target. This makes SPF alignment pass.
  - If using Cloudflare DNS: set these CNAMEs to DNS only (gray cloud). Proxying breaks DKIM/SPF validation.
- SPF for the sending domain/subdomain: publish or update SPF to include SendGrid.
  - Example for subdomain `alerts.fxlabsprime.com`: `v=spf1 include:sendgrid.net -all`
  - If the root domain already has an SPF for other services (e.g., Microsoft 365), include both as needed: `v=spf1 include:spf.protection.outlook.com include:sendgrid.net -all`
- DMARC for the sending domain/subdomain: start permissive, then tighten.
  - Example: `v=DMARC1; p=none; rua=mailto:dmarc@fxlabsprime.com; adkim=s; aspf=s; pct=100`
  - After validation, move to `p=quarantine` → `p=reject` to reduce spoofing.
- From address must match the authenticated domain: send from `alerts@alerts.fxlabsprime.com` if that's the domain you authenticated (update `FROM_EMAIL`).
- Optional: BIMI (brand logo) can help, but only after DMARC passes with enforcement and, ideally, a VMC.

Why Outlook flagged it:
- Without DKIM/SPF alignment for the From domain, DMARC fails or is unverifiable. Outlook/O365 then shows the warning and often places the message in Junk.

Code defaults updated:
- No code defaults for email sender. Set tenant-specific sender (`FXLABS_FROM_EMAIL` or `HEXTECH_FROM_EMAIL`) and configure your domain in SendGrid.
