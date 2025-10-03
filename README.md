# Fxlabs.ai Backend - Real-time Market Data Streaming Service

Re-architecture: See `REARCHITECTING.md` for the polling-only MT5 design. Today, the server streams tick and OHLC data over `/ws/market` (tick-driven, coalesced). Indicator streaming and periodic daily % change summaries are planned and tracked in the Implementation Checklist. No EA or external bridge required.

A high-performance, real-time financial market data streaming service built with Python, FastAPI, and MetaTrader 5 integration. Provides live forex data, OHLC candlestick streaming, AI-powered news analysis, and comprehensive alert systems for trading applications.

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

- **Real-time Data Streaming**: Live tick and OHLC data via WebSocket
- **Historical Data Access**: REST API for historical market data
- **AI-Powered News Analysis**: Automated economic news impact analysis (with live internet search)
- **Comprehensive Alert Systems**: Heatmap, RSI, and RSI Correlation alerts with email notifications
- **Smart Email Cooldown**: Value-based cooldown prevents spam while allowing significant RSI changes (email-level only; RSI Tracker pair-level cooldown removed)
- **Intelligent Caching**: Memory-efficient selective data caching
- **High Performance**: 99.9% bandwidth reduction through selective streaming
- **Scalable Architecture**: Async/await design for high concurrency
- **Per-Pair Concurrency Cap**: Keyed async locks prevent concurrent evaluations for the same pair/timeframe across alert services
- **Warm-up & Stale-Data Protection**: Skips evaluations when latest bar is stale (>2× timeframe) and enforces indicator lookback (e.g., RSI series) before triggering
// Removed: Rate Limits + Digest (alerts send immediately subject to value-based cooldown)
- **IST Timezone Display**: Email timestamps are shown in Asia/Kolkata (IST) for user-friendly readability
  - FXLabs tenant: All alert emails are enforced to IST (Asia/Kolkata) regardless of host tz. If the OS tz database is missing, a robust +05:30 (IST) fallback is applied.
- **Unified Email Header**: All alert emails use a common green header `#07c05c` showing `[FxLabs logo] FXLabs • <Alert Type> • <Local Date IST> • <Local Time IST>` (time in small font)
- **Single Common Footer**: Disclaimers are rendered once at the bottom of each email (not per pair/card). RSI/Correlation use "Not financial advice. © FxLabs AI". Heatmap and Indicator trackers use "Education only. © FxLabs AI".
- **Style‑Weighted Buy Now %**: Heatmap alerts compute a style‑weighted Final Score across selected timeframes and convert it to Buy Now % for triggers, per the Calculations Reference (EMA21/50/200, MACD, RSI, UTBot, Ichimoku; new‑signal boost; quiet‑market damping)
  - Per‑alert overrides: optional `style_weights_override` map customizes TF weights (only applied to selected TFs; invalid entries ignored; defaults used if sum ≤ 0).

## 📐 Calculations Alignment

This backend aligns alert evaluations with the Calculations Reference used by the frontend widgets:

- Closed‑candle policy: All RSI/correlation/heatmap evaluations use closed candles; forming candles are not used in triggers.
- MT5 OHLC snapshots still include the forming candle as the final element with `is_closed=false`. Backend RSI calculations ignore it automatically, so frontend collectors can continue using the tail for live charting without custom trimming.
- RSI (14, Wilder): Computed from MT5 OHLC (Bid‑based series), matching frontend logic.
- RSI Correlation (Dashboard parity):
  - Mode `rsi_threshold`: Pair‑type aware mismatch (positive: OB/OS split; negative: both OB or both OS).
  - Mode `real_correlation`: Timestamp‑aligned log‑return Pearson correlation over the rolling window. Mismatch thresholds are pair‑type aware (positive: corr < +0.25; negative: corr > −0.15). Strength labels: strong |corr|≥0.70, moderate ≥0.30, else weak.
- Heatmap/Quantum aggregation:
  - Indicators: EMA21/50/200, MACD(12,26,9), RSI(14), UTBot(EMA50 ± 3×ATR10), Ichimoku Clone (9/26/52).
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
JBLANKED_API_URL=https://www.jblanked.com/news/api/forex-factory/calendar/week/
JBLANKED_API_KEY=your_jblanked_key
NEWS_UPDATE_INTERVAL_HOURS=0.5  # 30 minutes
JBLANKED_API_KEY=your_jblanked_key
NEWS_UPDATE_INTERVAL_HOURS=0.5  # 30 minutes
NEWS_CACHE_MAX_ITEMS=500

# Email Configuration (tenant-specific only; no global defaults)
# Define only the variables for the tenant you run.
# FXLabs
FXLABS_SENDGRID_API_KEY=
FXLABS_FROM_EMAIL=alerts@fxlabs.ai
FXLABS_FROM_NAME=FX Labs Alerts
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
# FXLabs
FXLABS_SUPABASE_URL=https://your-fxlabs.supabase.co
FXLABS_SUPABASE_SERVICE_KEY=
FXLABS_SENDGRID_API_KEY=
FXLABS_FROM_EMAIL=alerts@fxlabs.ai
FXLABS_FROM_NAME=FX Labs Alerts
FXLABS_PUBLIC_BASE_URL=https://api.fxlabs.ai
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
  - Core signals in the daily brief use `scalper` mode for Quantum analysis.
- For observability, the batch log includes a CSV of recipient emails and count.

#### News Reminder Behavior (High‑Impact Only)
- The 5‑minute news reminder now filters to only AI‑normalized high‑impact items (`impact == "high"`). Medium/low impact items are skipped.
- Source impact values and AI analysis are normalized to `high|medium|low`; only `high` qualifies for reminders.
 - Branding: News reminder emails now use the same unified green header and common footer as RSI/Correlation emails (logo + date/time in header; single disclaimer footer).

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

### WebSocket Endpoints

#### Market Data WebSocket (`/ws/market`) — deprecated soon
- **URL**: `ws://localhost:8000/ws/market`
- **Purpose**: Real-time tick and OHLC data streaming
- **Features**: Selective timeframe subscriptions, intelligent caching, Bid/Ask parallel OHLC fields

Note on closed-bar guarantees:
- The server emits an `is_closed=true` OHLC bar at every timeframe boundary (checked at ~100 ms resolution), including quiet 1‑minute windows with zero ticks. This removes client-side heuristics and eliminates RSI drift between live streaming and initial snapshots.

Tick push payloads to clients remain a list of ticks. Internally, for alert checks, ticks are converted to a map keyed by symbol for consistency across services.
Connected discovery message includes Bid/Ask capabilities and schema:

```json
{
  "type": "connected",
  "message": "WebSocket connected successfully",
  "supported_timeframes": ["1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W"],
  "supported_data_types": ["ticks", "ohlc"],
  "supported_price_bases": ["last", "bid", "ask"],
  "ohlc_schema": "parallel"
}
```

Extended subscribe supports price basis and schema shaping (defaults: `last`, `parallel`):

```json
{
  "action": "subscribe",
  "symbol": "EURUSD",
  "timeframe": "1M",
  "data_types": ["ohlc", "ticks"],
  "price_basis": "bid",
  "ohlc_schema": "parallel"
}
```

Multi-timeframe subscriptions per symbol:
- A single WebSocket connection can subscribe to the same `symbol` across multiple `timeframe`s concurrently (e.g., `1M`, `5M`, `1H`, `4H`).
- Send a separate `subscribe` message per timeframe; each will stream its own initial snapshot, high‑frequency `ohlc_live` updates while the candle is forming, and a boundary `ohlc_update` when the candle closes.
- To unsubscribe a specific symbol×timeframe, send:

```json
{ "action": "unsubscribe", "symbol": "EURUSD", "timeframe": "4H" }
```

- To unsubscribe all timeframes for a symbol, omit `timeframe`:

```json
{ "action": "unsubscribe", "symbol": "EURUSD" }
```

OHLC payload types

- Live forming bar (high frequency, ~every tick, pushed within ~100 ms loop):

```json
{
  "type": "ohlc_live",
  "data": {
    "symbol": "EURUSD",
    "timeframe": "1M",
    "time": 1738219500000,
    "open": 1.1052, "high": 1.1056, "low": 1.1050, "close": 1.1054,
    "openBid": 1.1051, "highBid": 1.1055, "lowBid": 1.1049, "closeBid": 1.1053,
    "openAsk": 1.1053, "highAsk": 1.1057, "lowAsk": 1.1051, "closeAsk": 1.1055,
    "is_closed": false
  }
}
```

- Closed bar at timeframe boundary (guaranteed even if no ticks):

```json
{
  "type": "ohlc_update",
  "data": {
    "symbol": "EURUSD",
    "timeframe": "1M",
    "time": 1738219500000,
    "open": 1.1052, "high": 1.1056, "low": 1.1050, "close": 1.1054,
    "openBid": 1.1051, "highBid": 1.1055, "lowBid": 1.1049, "closeBid": 1.1053,
    "openAsk": 1.1053, "highAsk": 1.1057, "lowAsk": 1.1051, "closeAsk": 1.1055,
    "is_closed": true
  }
}
```

If a client requests `ohlc_schema = "basis_only"`, payloads omit parallel fields and map the requested basis into the canonical `open/high/low/close` keys.

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

#### Legacy Tick WebSocket (`/ws/ticks`)
- **URL**: `ws://localhost:8000/ws/ticks`
- **Purpose**: Backward-compatible tick-only streaming
- **Features**: Legacy client support

#### Market Data WebSocket v2 (`/market-v2`)
- Use `/market-v2` for new clients. It exposes tick and indicator payloads only (no OHLC streaming), and advertises capabilities via `supported_data_types` in the greeting.
- Current capabilities: `supported_data_types = ["ticks","indicators"]`.
- Broadcast-All mode: v2 pushes ticks and indicators (closed‑bar) for a baseline set of symbols/timeframes to all connected clients without explicit subscriptions. OHLC is computed server‑side only for indicators/alerts.
  - Baseline symbols: `RSI_SUPPORTED_SYMBOLS` from `app/constants.py` (broker‑suffixed)
  - Baseline timeframes: M1, M5, M15, M30, H1, H4, D1
- Subscribe remains optional and is primarily used to receive `initial_ohlc` / `initial_indicators` snapshots on demand.

Security and input validation (mirrors REST policy):
- If `API_TOKEN` is set, WebSocket connections must include header `X-API-Key: <token>`; otherwise connections are allowed without auth.
- Symbols allowlist: by default, only symbols in `RSI_SUPPORTED_SYMBOLS` are accepted. Override with env `WS_ALLOWED_SYMBOLS` (comma-separated, broker-suffixed).
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
  "indicators": {
    "rsi": {"method": "wilder", "applied_price": "close", "periods": [14]},
    "ema": {"periods": [21, 50, 200]},
    "macd": {"params": {"fast": 12, "slow": 26, "signal": 9}},
    "ichimoku": {"params": {"tenkan": 9, "kijun": 26, "senkou_b": 52, "displacement": 26}},
    "utbot": {"params": {"ema": 50, "atr": 10, "k": 3.0}}
  }
}
```
Tick payloads include `daily_change_pct` (Bid vs broker D1 reference):

```json
{"type": "ticks", "data": [ {"symbol":"EURUSDm","time":1696229945123,"time_iso":"2025-10-02T14:19:05.123Z","bid":1.06871,"ask":1.06885,"volume":120, "daily_change_pct": -0.12} ] }
```


##### Indicator payloads (when "indicators" is requested)

- On subscribe, the server sends the latest closed-bar snapshot:

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

- Live push when a new bar is detected by the 10s poller:

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

Note: `bar_time` is epoch milliseconds (ms) using broker server time.

### REST API Endpoints

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/health` | GET | Health check and MT5 status | No |
| `/api/ohlc/{symbol}` | GET | Historical OHLC data | Yes |
| `/api/rsi/{symbol}`  | GET | Closed‑bar RSI series (Wilder) aligned to OHLC | Yes |
| `/api/tick/{symbol}` | GET | Current tick data | Yes |
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

### Closed‑bar RSI REST (`/api/rsi/{symbol}`)

Parameters:
- `timeframe` (string): one of `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W` (default `5M`)
- `period` (int): RSI period (default `14`)
- `count` (int): number of OHLC bars to fetch for calculation (default `300`)

Response shape:
```json
{
  "symbol": "EURUSDm",
  "timeframe": "5M",
  "period": 14,
  "bars_used": 300,
  "count": 286,
  "times_ms": [1695200100000, ...],
  "times_iso": ["2025-09-20T12:35:00+00:00", ...],
  "rsi": [61.04, 62.15, ...],
  "applied_price": "close",
  "method": "wilder"
}
```

Notes:
- RSI is computed on closed bars only, matching MT5's default RSI(14) close/Wilder.
- `times_*` arrays align 1:1 with `rsi[]` and correspond to the closed bars beginning at index `period` in the closed OHLC sequence.
- For exact parity with MT5, request the broker‑suffixed symbol (e.g., `EURUSDm`).

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

### RSI Correlation Tracker — Threshold and Real Correlation

The RSI Correlation Tracker is available as a single per-user alert. Users select exactly one timeframe and a mode (`rsi_threshold` or `real_correlation`).
  - Startup warm‑up: On first observation per (pair_key, timeframe), baseline the last closed bar and current mismatch state; trigger only when a new bar produces a transition into mismatch.
  - Implementation: RSI Threshold mode reads closed‑bar RSI values from the centralized `indicator_cache`. When the cache is not yet warm for a `(symbol,timeframe,period)`, the service computes RSI from OHLC closed bars, updates the cache with the latest value (ring buffer), and then evaluates. This avoids duplicate math long‑term while ensuring immediate correctness.

- Pair selection is not required; backend evaluates a fixed set of correlation pair keys from `app/constants.py`.
- Triggers insert into `rsi_correlation_tracker_alert_triggers` and emails reuse a compact template.

Closed‑bar evaluation & retriggering:
- Evaluation runs once per new closed bar per correlation pair/timeframe by checking the last closed timestamps of both symbols; the service evaluates when a new bar is seen and avoids duplicate evaluations within the same closed bar.
- Retrigger only after the pair returns to non‑mismatch and then transitions into mismatch again on a subsequent closed bar.

### Global Limit: Max 3 Pairs/User

- The backend now enforces a global cap of 3 unique symbols per user across all active alerts (Heatmap, RSI, and RSI Correlation).
- Enforcement occurs on alert creation endpoints:
  - `POST /api/heatmap-alerts`
  - `POST /api/rsi-alerts`
  - `POST /api/rsi-correlation-alerts` (both symbols in each correlation pair are counted)
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

### RSI Correlation Alerts — Threshold and Real Correlation

- Modes:
  - RSI Threshold: evaluate pairwise RSI combinations (e.g., positive/negative mismatches, neutral break) using per‑pair RSI settings.
  - Real Correlation: compute Pearson correlation of returns over a configurable `correlation_window` (default 50) using historical OHLC closes for both symbols.
- Outputs include RSI values (threshold mode) or `correlation_value` (real correlation mode), with per‑pair details in emails.
  - Email uses a compact, mobile‑friendly HTML template titled "RSI Correlation Mismatch" with columns: Expected, RSI Corr Now, Trigger.

#### Email Template (RSI Correlation — Threshold Mode)
- Compact per‑pair card with RSI correlation summary.
- Fields per card:
  - **pair_a/pair_b**: `symbol1`/`symbol2`
  - **rsi_len**: `rsi_period`
  - **timeframe**: `timeframe`
  - **expected_corr**: derived from `trigger_condition` using OB/OS thresholds:
    - positive_mismatch: `one ≥ overbought` and `one ≤ oversold`
    - negative_mismatch: `both ≥ overbought` or `both ≤ oversold`
    - neutral_break: `both between oversold and overbought`
  - **rsi_corr_now**: correlation between recent RSI series for the pair (if computed)
  - **trigger_rule**: humanized `trigger_condition`
Notes:
- Multiple triggered pairs render as multiple cards in one email.

#### Email Template (Real Correlation)
- Uses a compact, mobile‑friendly HTML card per triggered pair.
- Fields per card:
- **pair_a/pair_b**: Symbols displayed as `ABC/DEF` (e.g., `EUR/USD` vs `GBP/USD`)
  - **lookback**: `correlation_window` from alert config
  - **timeframe**: TF of the evaluation (e.g., `1H`)
  - **expected_corr**: Threshold expression derived from the triggered rule:
    - strong_positive: `≥ strong_correlation_threshold`
    - strong_negative: `≤ -strong_correlation_threshold`
    - weak_correlation: `|corr| ≤ weak_correlation_threshold`
    - correlation_break: `moderate_threshold ≤ |corr| < strong_threshold`
  - **actual_corr**: Calculated `correlation_value` (rounded)
  - **trigger_rule**: Humanized `trigger_condition` (e.g., `Strong positive correlation`)

Notes:
- Multiple triggered pairs render as multiple cards within a single email.
- Subject remains `Trading Alert: <alert_name>` and a text/plain alternative is included.

### Heatmap Alerts — Final Score & Buy Now % (Style‑Weighted)

- Per‑timeframe indicator strength is normalized to a score in [−100..+100].
- Startup warm‑up: For the Tracker, armed state per (alert, symbol) is initialized from current Buy%/Sell% (sides already above thresholds start disarmed) and the first observation is skipped. For the Custom Indicator Tracker, the last signal per (alert, symbol, timeframe, indicator) is baselined and the first observation is skipped.
- Style weighting aggregates across the alert's selected timeframes:
  - Scalper: 1M(0.2), 5M(0.4), 15M(0.3), 30M(0.1)
  - Swing: 1H(0.25), 4H(0.45), 1D(0.3)
- Final Score = weighted average of per‑TF scores; Buy Now % = (Final Score + 100)/2.

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

See `ALERTS.md` for canonical Supabase table schemas and exact frontend implementation requirements (Type A/Type B/RSI/RSI‑Correlation), including field lists, endpoints, validation, and delivery channel setup.
- Re‑arm on exit then re‑cross: once fired, do not re‑fire while the condition persists; re‑arm after leaving the zone and fire again only on a new cross‑in. Changing the configured threshold re‑arms immediately.
- Cooldowns, concurrency, and alert frequency (once/hourly/daily) apply consistently across alert types. Per-user rate limits and digest have been removed.

See `ALERTS.md` for the consolidated alerts product & tech spec.

### Troubleshooting: Only RSI Tracker and Daily emails arrive
- No active alerts: Ensure you have rows in Supabase for `heatmap_tracker_alerts`, `heatmap_indicator_tracker_alerts`, or `rsi_correlation_tracker_alerts` with `is_active=true` and non‑empty `pairs` (max 3).
- Thresholds too strict: For Heatmap, start with Buy≥70 / Sell≤30. On higher TFs, RSI may hover mid‑band for long periods.
- Arm/disarm gating: After a trigger, that side disarms and rearms only after leaving the zone (threshold−5).
- Correlation triggers: Fire only on transitions into a mismatch state; steady regimes won't re‑trigger.

### 📰 News API Usage (External Source + Internal Endpoints)

#### External Source: Jblanked (Forex Factory Calendar - Weekly)
- URL (default): `https://www.jblanked.com/news/api/forex-factory/calendar/week/`
- Auth: `Authorization: Api-Key <JBLANKED_API_KEY>`
- Method: GET

Example:
```bash
export JBLANKED_API_URL="https://www.jblanked.com/news/api/forex-factory/calendar/week/"
export JBLANKED_API_KEY="<your_jblanked_key>"

curl -s \
  -H "Authorization: Api-Key $JBLANKED_API_KEY" \
  -H "Content-Type: application/json" \
  "$JBLANKED_API_URL" | jq .
```

Field mapping tolerated (multiple shapes):
- headline: `Name | title | headline | name`
- forecast: `Forecast | forecast | expected`
- previous: `Previous | previous | prev`
- actual: `Actual | actual | result`
- currency: `Currency | currency | ccy | country`
- impact: `Strength | impact | importance`
- time: `Date | time | date | timestamp` → Converted from upstream UTC+3 to UTC ISO (Z)
- optional context: `Outcome`, `Quality` appended to headline

Timezones:
- Upstream: UTC+3 (as provided by Jblanked)
- Processing/Serving: converted to UTC ISO8601 with Z

Cache policy (weekly merge & dedup):
- Fetch weekly data and normalize times to UTC.
- Deduplicate using key: `(currency, UTC time, base headline)` where base headline excludes appended `(Outcome)` and ` - Quality`.
- If a matching item reappears with changed `Outcome`, `Quality`, or `Actual`, refresh its AI analysis; otherwise, keep existing analysis (no duplicate calls).
- After merge: sort cache by `time` (UTC) descending and trim to `NEWS_CACHE_MAX_ITEMS`.

### Example Usage

#### WebSocket Connection (JavaScript)
```javascript
const ws = new WebSocket('ws://localhost:8000/market-v2');

ws.onopen = () => {
    // Subscribe to EURUSD 1-minute data (alerts enforce 5M+ only)
    ws.send(JSON.stringify({
        action: 'subscribe',
        symbol: 'EURUSD',
        timeframe: '1M',
        data_types: ['ticks', 'indicators']
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
};
```

#### REST API Request
```bash
# Get historical OHLC data
curl -H "X-API-Key: your_token" \
     "http://localhost:8000/api/ohlc/EURUSD?timeframe=1H&count=100"

# Get current tick data
curl -H "X-API-Key: your_token" \
     "http://localhost:8000/api/tick/EURUSD"
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
   WebSocket Connection → Authentication → Subscription → Data Streaming
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

### Test HTML Client
Open `test_websocket.html` in your browser to test WebSocket connections interactively.

## 🚀 Deployment

### Production Deployment

The system is configured for production deployment with Cloudflare Tunnel:

```yaml
# config.yml
tunnel: 5612346e-ee13-4f7b-8a04-9215b63b14d3
ingress:
  - hostname: api.fxlabs.ai
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
│   ├── rsi_correlation_tracker_alert_service.py # RSI correlation alert processing
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
- WebSocket endpoints: `/ws/market` (preferred) and `/ws/ticks` (legacy). See `test_websocket_client.py` for usage.

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
- **Tick Data**: Real-time price updates (bid, ask, last, volume)
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
- `analysis.impact`: high | medium | low (lowercase)
- Removed fields: `currencies_impacted`, `currency_pairs`

Model behavior:
- The Perplexity prompt enforces pre‑release evaluation with taxonomy‑based impact and policy‑aware directional bias. Full prompt used:
  ```
  You are a Forex macro event classifier used BEFORE an economic release. Output exactly:
  {
    "effect": "bullish|bearish|neutral",
    "impact": "high|medium|low",
    "explanation": "<max 2 sentences>"
  }
  Constraints:
  - Lowercase enums only.
  - No extra fields or text.

  INPUT
  Currency: {news_item.currency}
  News: {news_item.headline}
  Time: {news_item.time or 'N/A'}
  Forecast: {news_item.forecast or 'N/A'}
  Previous: {news_item.previous or 'N/A'}
  Source impact hint: {news_item.impact or 'N/A'}

  A) IMPACT (magnitude tier, not direction)
  1) If Source impact hint ∈ {High, Medium, Low}, mirror it (lowercased) UNLESS it contradicts the taxonomy below by >1 tier; in that case, prefer the taxonomy.
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
- Parsing first attempts to load the JSON. If unavailable, it falls back to regex and synonym detection.
- `impact` is normalized from synonyms (e.g., significant→high, moderate→medium, minor→low), then falls back to the source `impact` field if present; defaults to `medium` if still ambiguous.

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
  - External API keys (Perplexity/Jblanked) are expected via env; missing keys will limit news analysis. Behavior unchanged.
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
- `LIVE_RSI_DEBUGGING` — emits periodic closed‑bar RSI for BTC/USD 1M (default `false`).
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

- #### Live RSI Debugging cadence (M1 closed‑bar, cache‑aligned)
- `🧭 liveRSI` logs are emitted directly from the indicator scheduler when it detects a new closed `1M` bar for `BTCUSDm` only. Values are sourced from the same indicator pipeline and cache used by alerts and WebSocket indicator streaming (single source of truth).
- When `LIVE_RSI_DEBUGGING=true`, logs appear shortly after each M1 close (sub‑100 ms latency target).
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
- RSI Correlation Tracker
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
- `rsi_correlation_eval | triggers: <n>`
- `heatmap_tracker_eval | triggers: <n>`
- `indicator_tracker_eval | triggers: <n>`

#### Human‑Readable Emoji Logging (v2.2.0)
Alert evaluations and actions are now logged in a clean, human‑readable format with emojis using `app/alert_logging.py`.

Key events (examples):
- `🎯 rsi_tracker_triggers | alert_id: abc123 | count: 3`
- `📤 email_queue | alert_type: rsi | alert_id: abc123`
- `⚠️ market_data_stale | symbol: EURUSD | age_minutes: 12`
- `📝 db_trigger_logged | alert_id: abc123 | symbol: EURUSD`

Notes:
- Complex objects (lists/dicts) are not dumped; shown as `…` to avoid noisy logs and accidental payload leaks.
- Logs are optimized for humans in terminals, not JSON processors.
- Email send failures no longer log raw provider response bodies.
- Third‑party verbose clients (e.g., SendGrid `python_http_client`) are set to WARNING to suppress payload dumps. To see them again, manually set `logging.getLogger("python_http_client").setLevel(logging.DEBUG)` in your session.

Modules instrumented: `rsi_alert_service`, `rsi_tracker_alert_service`, `rsi_correlation_tracker_alert_service`, `heatmap_tracker_alert_service`, `heatmap_indicator_tracker_alert_service`, `alert_cache`, and `email_service` (queue/send summaries).

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
- **RSI Correlation Alert Service**: Changed "Check Complete: X alerts processed, 0 triggered" from INFO to DEBUG level
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
- **Multi-alert support**: Works with RSI, Heatmap, and RSI Correlation alerts
- **Smart value extraction**: Handles different data structures for each alert type
- **Hash generation**: Includes actual values (RSI, strength, correlation) rounded to 1 decimal
- **Value comparison**: Compares current vs last sent values for breakthrough detection
- **Breakthrough logic**: If any value difference ≥ threshold, alert is sent
- **Memory management**: Automatic cleanup prevents memory leaks

### Alert Type Support
- **RSI Alerts**: Tracks `rsi` values (e.g., 70.1 → 75.1 = breakthrough)
- **Heatmap Alerts**: Tracks `strength` values and RSI from indicators
- **RSI Correlation Alerts**: Tracks `rsi1` and `rsi2` values separately

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
### Ctrl+C hangs at "Waiting for application shutdown"
- Symptom: After pressing Ctrl+C, logs show `INFO:     Shutting down`, `connection closed` lines, and then `INFO:     Waiting for application shutdown.` without exiting.
- Cause: Background schedulers (e.g., indicators/news) must be cancelled so the lifespan shutdown can complete. If any long-running task isn't cancelled, the server waits indefinitely.
- Fix in code: The shutdown sequence cancels all background tasks. Ensure you are on the latest code where `server.py` cancels scheduler tasks with proper `CancelledError` handling.
- Tip: If you still see a hang, check for any custom added loops without `CancelledError` handling. All loops should `await asyncio.sleep(...)` and properly handle `asyncio.CancelledError`.

### "RSI Tracker: triggers exist in DB but no emails/logs"
- **What it means**: Records appear in `rsi_tracker_alert_triggers`, but you don't see corresponding console logs or emails.
- **Why it happened previously**: The tracker didn't log info-level messages on successful triggers, and exceptions during email scheduling could be swallowed without a visible log.
- **Fix in code**: The tracker now logs when triggers are detected and when emails are queued, and logs any exceptions during email scheduling.
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

### "RSI Correlation Tracker: triggers exist in DB but no emails/logs"
- **Symptom**: Rows appear in `rsi_correlation_tracker_alert_triggers` but you don't see emails or logs.
- **Fix in code**: The correlation tracker now logs when a trigger occurs and when an email is queued; errors are logged during send.
- **Checklist**:
  - **Pairs configured**: Set `RSI_CORR_TRACKER_DEFAULT_PAIRS` (e.g., `EURUSD_GBPUSD,USDJPY_GBPUSD`).
  - **notification_methods**: Ensure includes `"email"`.
  - **Email service configured** and **LOG_LEVEL** set appropriately.
  - **Mode & thresholds**: `mode` is `rsi_threshold` or `real_correlation`; for real correlation, we treat `|corr| < 0.25` as mismatch by default.
- **Expected logs**:
  - `🚨 RSI Correlation Tracker trigger: ...`
  - `📤 Queueing email send for RSI Correlation Tracker ...`

### "SendGrid not configured, skipping RSI alert email"
- Cause: `EmailService` didn't initialize a SendGrid client (`self.sg is None`). This happens when either the SendGrid library isn't installed or tenant-specific credentials are missing.
- Fix quickly:
  - Install deps in your venv: `pip install -r requirements.txt` (includes `sendgrid`)
  - Provide tenant-specific credentials via environment or `.env` (auto-loaded now):
    - FXLabs: `FXLABS_SENDGRID_API_KEY=...`, `FXLABS_FROM_EMAIL=verified@yourdomain.com`, `FXLABS_FROM_NAME=FX Labs Alerts`
    - HexTech: `HEXTECH_SENDGRID_API_KEY=...`, `HEXTECH_FROM_EMAIL=verified@yourdomain.com`, `HEXTECH_FROM_NAME=HexTech Alerts`
  - Ensure your process actually sees the variables:
    - macOS/Linux: `.env` is auto-loaded; no manual `export` needed
    - Windows: `start.ps1`/`start.bat` also load `.env`
  - Verify your SendGrid sender: Single Sender verification or Domain Authentication, otherwise SendGrid returns 400/403 and emails won't send.
- Where to set: copy `config.env.example` to `.env` and fill values, or set env vars directly in your deployment.

### "HTTP Error 403: Forbidden" during send (intermittent)
- Symptom: Logs show `❌ Error sending ... email: HTTP Error 403: Forbidden` while other emails sometimes succeed.
- Most common root causes:
  - Sender identity mismatch: `FROM_EMAIL` is not a verified Single Sender or part of an authenticated domain. If only `alerts@fxlabs.ai` is verified, sending from `alerts@alerts.fxlabs.ai` will 403. The code requires tenant-specific `FROM_EMAIL`; no default is used.
  - API key scope too narrow: The `SENDGRID_API_KEY` lacks the `Mail Send` permission. Regenerate with Full Access or include `Mail Send`.
  - IP Access Management: If enabled in SendGrid, requests from non-whitelisted IPs are blocked with 403. Whitelist the server IP(s).
  - Region mismatch: EU-only accounts must use the EU endpoint; ensure your environment uses the correct SendGrid region (contact SendGrid if unsure).
- Why intermittent? Different processes or shells might pick up different env files. Ensure you set the tenant-specific variables (`FXLABS_*` for FXLabs or `HEXTECH_*` for HexTech) in the active environment for that process. No code defaults are used.
- What we log now (for failures): status, a trimmed response body, masked API key, and `from/to` addresses to speed up diagnosis without leaking secrets.
- Quick checklist:
  - Confirm your env defines tenant-specific keys: for FXLabs use `FXLABS_SENDGRID_API_KEY`, `FXLABS_FROM_EMAIL`, `FXLABS_FROM_NAME`; for HexTech use `HEXTECH_SENDGRID_API_KEY`, `HEXTECH_FROM_EMAIL`, `HEXTECH_FROM_NAME`.
  - Verify the sender identity in SendGrid (Single Sender) or authenticate the `fxlabs.ai` domain.
  - If you use IP Access Management, add the server IP.
  - In SendGrid → API Keys, confirm the key includes `Mail Send`.
  - Run `python send_test_email.py you@example.com` (in an environment where running is allowed) to verify the path end-to-end.

#### Email Configuration Diagnostics (enhanced logs)
When email sending is disabled, the service now emits structured diagnostics showing what's missing or invalid (without leaking secrets). Example:

```
⚠️ Email service not configured — RSI alert email
   1) sendgrid library not installed (pip install sendgrid)
   2) Tenant API key missing (set FXLABS_SENDGRID_API_KEY or HEXTECH_SENDGRID_API_KEY)
   Values (masked): SENDGRID_API_KEY=SG.************abcd, FROM_EMAIL=alerts@fxlabs.ai, FROM_NAME=FX Labs
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
# For FXLabs
FXLABS_SENDGRID_API_KEY=your_sendgrid_api_key
FXLABS_FROM_EMAIL=alerts@fxlabs.ai
FXLABS_FROM_NAME=FX Labs Alerts

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
  - Complete Domain Authentication (CNAMEs) and send from an aligned subdomain (e.g., `alerts@alerts.fxlabs.ai`).
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
- Keep `FROM_EMAIL` on your authenticated domain/subdomain (e.g., alerts@alerts.fxlabs.ai).
- Avoid URL shorteners and excessive links in alert content.
- DMARC alignment: after verifying inboxing, move DMARC policy from `p=none` to `quarantine`/`reject` gradually.

### Outlook/Office 365: "We can't verify this email came from the sender"
This is a DMARC alignment/authentication issue. Fix by authenticating the domain and aligning the From address.

Checklist:
- Domain Authentication in SendGrid: Settings → Sender Authentication → Domain Authentication. Choose a dedicated subdomain (e.g., `alerts.fxlabs.ai`).
  - Add the DKIM CNAMEs SendGrid provides (typically `s1._domainkey.alerts.fxlabs.ai` and `s2._domainkey.alerts.fxlabs.ai`).
  - Enable "Custom Return Path" (bounce domain), e.g., `em.alerts.fxlabs.ai` CNAME to SendGrid target. This makes SPF alignment pass.
  - If using Cloudflare DNS: set these CNAMEs to DNS only (gray cloud). Proxying breaks DKIM/SPF validation.
- SPF for the sending domain/subdomain: publish or update SPF to include SendGrid.
  - Example for subdomain `alerts.fxlabs.ai`: `v=spf1 include:sendgrid.net -all`
  - If the root domain already has an SPF for other services (e.g., Microsoft 365), include both as needed: `v=spf1 include:spf.protection.outlook.com include:sendgrid.net -all`
- DMARC for the sending domain/subdomain: start permissive, then tighten.
  - Example: `v=DMARC1; p=none; rua=mailto:dmarc@fxlabs.ai; adkim=s; aspf=s; pct=100`
  - After validation, move to `p=quarantine` → `p=reject` to reduce spoofing.
- From address must match the authenticated domain: send from `alerts@alerts.fxlabs.ai` if that's the domain you authenticated (update `FROM_EMAIL`).
- Optional: BIMI (brand logo) can help, but only after DMARC passes with enforcement and, ideally, a VMC.

Why Outlook flagged it:
- Without DKIM/SPF alignment for the From domain, DMARC fails or is unverifiable. Outlook/O365 then shows the warning and often places the message in Junk.

Code defaults updated:
- No code defaults for email sender. Set tenant-specific sender (`FXLABS_FROM_EMAIL` or `HEXTECH_FROM_EMAIL`) and configure your domain in SendGrid.
