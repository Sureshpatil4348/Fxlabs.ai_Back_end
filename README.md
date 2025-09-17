# Fxlabs.ai Backend - Real-time Market Data Streaming Service

A high-performance, real-time financial market data streaming service built with Python, FastAPI, and MetaTrader 5 integration. Provides live forex data, OHLC candlestick streaming, and AI-powered news analysis for trading applications.

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
- **Intelligent Caching**: Memory-efficient selective data caching
- **High Performance**: 99.9% bandwidth reduction through selective streaming
- **Scalable Architecture**: Async/await design for high concurrency

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
NEWS_UPDATE_INTERVAL_HOURS=24
NEWS_CACHE_MAX_ITEMS=100
```

## 📡 API Documentation

### WebSocket Endpoints

#### Market Data WebSocket (`/ws/market`)
- **URL**: `ws://localhost:8000/ws/market`
- **Purpose**: Real-time tick and OHLC data streaming
- **Features**: Selective timeframe subscriptions, intelligent caching

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
│   ├── __init__.py             # App package
│   ├── config.py               # Env-backed configuration (no functional change)
│   ├── models.py               # Pydantic models and enums
│   ├── mt5_utils.py            # MT5 helpers, OHLC cache, timeframe logic
│   └── news.py                 # News fetching, AI analysis, scheduler, cache
├── server.py                   # FastAPI app, routes & websockets (imports from app/*)
├── requirements.txt            # Python dependencies
├── config.yml                  # Cloudflare tunnel config
├── config.env.example          # Environment variables template
├── test_websocket_client.py    # WebSocket test client
├── test_websocket.html         # HTML test client
└── README.md                   # This file
```

The modular structure isolates responsibilities while preserving all existing behavior and endpoints. Environment variable names and usage remain unchanged.

### Key Dependencies
- **FastAPI**: Web framework with async support
- **MetaTrader5**: MT5 Python API integration
- **WebSockets**: Real-time communication
- **Pydantic**: Data validation and serialization
- **aiohttp**: Async HTTP client for external APIs

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
The system provides comprehensive logging for:
- Connection events
- Data processing errors
- API request/response cycles
- Performance metrics

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For support and questions:
- Create an issue in the repository
- Check the troubleshooting section in README_OHLC.md
- Review the test files for usage examples

---

**Version**: 2.0.0  
**Last Updated**: December 2024  
**Compatibility**: Python 3.8+, MT5 Python API, FastAPI 0.100+