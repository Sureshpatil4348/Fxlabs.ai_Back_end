# Fxlabs.ai Backend - Real-time Market Data Streaming Service

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
- **Smart Email Cooldown**: Value-based cooldown prevents spam while allowing significant RSI changes
- **Intelligent Caching**: Memory-efficient selective data caching
- **High Performance**: 99.9% bandwidth reduction through selective streaming
- **Scalable Architecture**: Async/await design for high concurrency
- **Per-Pair Concurrency Cap**: Keyed async locks prevent concurrent evaluations for the same pair/timeframe across alert services
- **Warm-up & Stale-Data Protection**: Skips evaluations when latest bar is stale (>2× timeframe) and enforces indicator lookback (e.g., RSI series) before triggering
- **Rate Limits + Digest**: Caps alert emails to 5/hour per user (successful sends only); overflows are batched into a single digest email
- **IST Timezone Display**: Email timestamps are shown in Asia/Kolkata (IST) for user-friendly readability
- **Style‑Weighted Buy Now %**: Heatmap alerts compute a style‑weighted Final Score across selected timeframes and convert it to Buy Now % for triggers
  - Per-alert overrides: optional `style_weights_override` map customizes TF weights (only applied to selected TFs; invalid entries ignored; defaults used if sum ≤ 0).

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
# Start the server
python server.py
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

Create a `.env` file with the following variables:

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
NEWS_UPDATE_INTERVAL_HOURS=1
JBLANKED_API_KEY=your_jblanked_key
NEWS_UPDATE_INTERVAL_HOURS=1
NEWS_CACHE_MAX_ITEMS=500

# Alert System Configuration
SENDGRID_API_KEY=your_sendgrid_api_key
FROM_EMAIL=alerts@fxlabs.ai
FROM_NAME=FX Labs Alerts
PUBLIC_BASE_URL=https://api.fxlabs.ai
UNSUBSCRIBE_SECRET=change_me_to_a_random_secret
UNSUBSCRIBE_STORE_FILE=/var/fxlabs/unsubscribes.json
```

#### Environment Loading (.env)
- The app now auto-loads `.env` via `python-dotenv` in `app/config.py`.
- Place your `.env` at the project root (same folder as `server.py`).
- Existing process environment variables are not overridden (safe-by-default).
- This fixes cases where macOS/Linux sessions didn’t see `SENDGRID_API_KEY` unless exported manually.

## 📡 API Documentation

### WebSocket Endpoints

#### Market Data WebSocket (`/ws/market`)
- **URL**: `ws://localhost:8000/ws/market`
- **Purpose**: Real-time tick and OHLC data streaming
- **Features**: Selective timeframe subscriptions, intelligent caching

Tick push payloads to clients remain a list of ticks. Internally, for alert checks, ticks are converted to a map keyed by symbol for consistency across services.

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

#### Legacy Tick WebSocket (`/ws/ticks`)
- **URL**: `ws://localhost:8000/ws/ticks`
- **Purpose**: Backward-compatible tick-only streaming
- **Features**: Legacy client support

### REST API Endpoints

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/health` | GET | Health check and MT5 status | No |
| `/api/ohlc/{symbol}` | GET | Historical OHLC data | Yes |
| `/api/tick/{symbol}` | GET | Current tick data | Yes |
| `/api/symbols` | GET | Symbol search | Yes |
| `/api/news/analysis` | GET | AI-analyzed news data | Yes |
| `/api/news/refresh` | POST | Manual news refresh | Yes |
| `/api/heatmap-alerts` | POST | Create heatmap alert | Yes |
| `/api/heatmap-alerts/user/{email}` | GET | Get user heatmap alerts | Yes |
| `/api/rsi-alerts` | POST | Create RSI alert | Yes |
| `/api/rsi-alerts/user/{email}` | GET | Get user RSI alerts | Yes |
| `/api/rsi-correlation-alerts` | POST | Create RSI correlation alert | Yes |
| `/api/rsi-correlation-alerts/user/{email}` | GET | Get user RSI correlation alerts | Yes |

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
  - **pair_a/pair_b**: Symbols (e.g., `EURUSD` vs `GBPUSD`)
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
- Style weighting aggregates across the alert’s selected timeframes:
  - Scalper: 1M(0.2), 5M(0.4), 15M(0.3), 30M(0.1)
  - Day: 15M(0.2), 30M(0.35), 1H(0.35), 4H(0.1)
  - Swing: 1H(0.25), 4H(0.45), 1D(0.3)
- Final Score = weighted average of per‑TF scores; Buy Now % = (Final Score + 100)/2.
- Triggers:
  - BUY if Buy Now % ≥ `buy_threshold_min` (and ≤ `buy_threshold_max` when provided)
  - SELL if Buy Now % ≤ `sell_threshold_max` (and ≥ `sell_threshold_min`)
 - Optional Minimum Alignment (N cells): require at least N timeframes to align with the chosen direction (TF strength ≥ buy_min for BUY, ≤ sell_max for SELL).
 - Cooldown: Per (alert, symbol, direction) cooldown window (default 30 minutes). You can override via `cooldown_minutes` on the alert.
- Indicator Flips (Type B): UTBOT, Ichimoku (Tenkan/Kijun), MACD, and EMA(21/50/200) flips supported with Only‑NEW K=3 and 1‑bar confirmation. Optional gate: require style‑weighted Buy Now % ≥ buy_min (BUY) or ≤ sell_max (SELL); defaults 60/40. Cooldown: per (pair, timeframe, indicator) using `cooldown_minutes` (default 30m).

### Alert Scheduling & Re‑triggering (Global)

- End‑of‑timeframe evaluation only: a unified scheduler triggers checks on timeframe boundaries (1M/5M/15M/30M/1H/4H/1D). Heatmap, RSI, and RSI Correlation are evaluated on TF closes; tick-driven checks are disabled by default.
- Crossing/Flip triggers: fire when the metric crosses into the condition from the opposite side (or a regime flip occurs), not on every bar while in‑zone.

See `ALERTS.md` for canonical Supabase table schemas and exact frontend implementation requirements (Type A/Type B/RSI/RSI‑Correlation), including field lists, endpoints, validation, and delivery channel setup.
- Re‑arm on exit then re‑cross: once fired, do not re‑fire while the condition persists; re‑arm after leaving the zone and fire again only on a new cross‑in. Changing the configured threshold re‑arms immediately.
- Rate limits, cooldowns, concurrency, and alert frequency (once/hourly/daily) apply consistently across alert types.

See `ALERTS.md` for the consolidated alerts product & tech spec.

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
const ws = new WebSocket('ws://localhost:8000/ws/market');

ws.onopen = () => {
    // Subscribe to EURUSD 1-minute data
    ws.send(JSON.stringify({
        action: 'subscribe',
        symbol: 'EURUSD',
        timeframe: '1M',
        data_types: ['ticks', 'ohlc']
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
        "1M": deque([100_OHLC_bars]),
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
│   └── rsi_correlation_alert_service.py # RSI correlation alert processing
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

### New Helpers

- `app/mt5_utils.py:get_current_tick(symbol: str) -> Optional[Tick]`
  - Ensures the symbol is selected and returns a `Tick` from `mt5.symbol_info_tick`.
  - Used by RSI alert services to avoid ImportErrors and simulated fallbacks.

## 📊 Supported Data Types

### Timeframes
- **1M** - 1 Minute
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
- The AI prompt requests a strict JSON-only reply to avoid ambiguity, and instructs the model to use live internet search to validate context:
  ```json
  {
    "effect": "bullish|bearish|neutral",
    "impact": "high|medium|low",
    "explanation": "<max 2 sentences>"
  }
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
  - Email rate limiting now counts only successfully delivered alert sends toward the 5/hour cap. Attempts skipped by cooldown/value-similarity do not consume quota. Digest emails are sent at most once per 60 minutes when overflow occurs.

- Low severity:
  - Logging is console-based; consider structured logging for production observability.
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
All logs now include timestamps with timezone offset using the format:
`YYYY-MM-DD HH:MM:SS±ZZZZ | LEVEL | module | message`.

You can control verbosity via `LOG_LEVEL` (default `INFO`).

The system provides comprehensive logging for:
- Connection events
- Data processing errors
- API request/response cycles
- Performance metrics

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

### "SendGrid not configured, skipping RSI alert email"
- Cause: `EmailService` didn't initialize a SendGrid client (`self.sg is None`). This happens when either the SendGrid library isn’t installed in your environment or `SENDGRID_API_KEY` isn’t present in the process environment.
- Fix quickly:
  - Install deps in your venv: `pip install -r requirements.txt` (includes `sendgrid`)
  - Provide credentials via environment or `.env` (auto-loaded now):
    - `SENDGRID_API_KEY=YOUR_REAL_SENDGRID_API_KEY`
    - `FROM_EMAIL=verified_sender@yourdomain.com` (must be a verified single sender or a domain verified in SendGrid)
    - `FROM_NAME=FX Labs Alerts` (optional)
  - Ensure your process actually sees the variables:
    - macOS/Linux: `.env` is auto-loaded; no manual `export` needed
    - Windows: `start.ps1`/`start.bat` also load `.env`
  - Verify your SendGrid sender: Single Sender verification or Domain Authentication, otherwise SendGrid returns 400/403 and emails won’t send.
- Where to set: copy `config.env.example` to `.env` and fill values, or set env vars directly in your deployment.

#### Email Configuration Diagnostics (enhanced logs)
When email sending is disabled, the service now emits structured diagnostics showing what’s missing or invalid (without leaking secrets). Example:

```
⚠️ Email service not configured — RSI alert email
   1) sendgrid library not installed (pip install sendgrid)
   2) SENDGRID_API_KEY missing (set in environment or .env)
   Values (masked): SENDGRID_API_KEY=SG.************abcd, FROM_EMAIL=alerts@fxlabs.ai, FROM_NAME=FX Labs
   Hint: copy config.env.example to .env and fill SENDGRID_API_KEY, FROM_EMAIL, FROM_NAME; python-dotenv auto-loads .env
```

Notes:
- API key is masked (prefix + last 4 chars) for safety.
- If the key doesn’t start with `SG.`, a hint is logged to double‑check the value.
- `rsi_alert_service` will also surface a one‑line summary under “Email diagnostics:” when an email send fails.

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
- Set SendGrid credentials in `.env` (or environment):
```env
SENDGRID_API_KEY=your_sendgrid_api_key
FROM_EMAIL=alerts@yourdomain.com
FROM_NAME=FX Labs Alerts
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
- Check suppression lists anyway: make sure the recipient isn’t present under Bounces/Blocks/Spam Reports; remove if found, then resend.
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

### Outlook/Office 365: "We can’t verify this email came from the sender"
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
- From address must match the authenticated domain: send from `alerts@alerts.fxlabs.ai` if that’s the domain you authenticated (update `FROM_EMAIL`).
- Optional: BIMI (brand logo) can help, but only after DMARC passes with enforcement and, ideally, a VMC.

Why Outlook flagged it:
- Without DKIM/SPF alignment for the From domain, DMARC fails or is unverifiable. Outlook/O365 then shows the warning and often places the message in Junk.

Code defaults updated:
- Defaults now prefer `FROM_EMAIL=alerts@alerts.fxlabs.ai` and add dual-part emails with transactional headers and one-click unsubscribe. Set your actual authenticated sender in `.env` and configure `PUBLIC_BASE_URL` and `UNSUBSCRIBE_SECRET`.
