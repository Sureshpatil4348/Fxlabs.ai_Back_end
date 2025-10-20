### API Documentation — WebSocket v2 and REST

This document describes how the frontend should consume market data and indicators from the backend, answers common integration questions, and specifies request/response structures.

### Answers to common questions

- **Mechanism to fetch indicators for different timeframes via WebSocket?**
  - Yes. WebSocket v2 (`/market-v2`) is broadcast-only. The server computes closed-bar indicators on a 10s cadence and pushes `indicator_update` events for all allowed symbols across baseline timeframes: `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`. It also broadcasts `currency_strength_update` snapshots over WebSocket only on closed bars and only for supported (WS-allowed) timeframes, using closed-candle ROC aggregation for the 8 fiat currencies and normalizing to a −100..100 scale (0 = neutral). Note: Currency Strength enforces a minimum timeframe of `5M` (no `1M`).
  - There is no per-client subscription filtering in v2. Clients receive broadcast updates when a new closed bar is detected.

- **Should the frontend use REST instead?**
  - Use both:
    - WebSocket v2 for live ticks, closed-bar `indicator_update`, and closed-bar `currency_strength_update` pushes (only for WS-allowed timeframes).
    - REST for initial state via `/api/indicator`.
  - v2 does not send initial OHLC or indicator snapshots on connect; fetch initial state via REST, then merge live pushes.

- **Does it work properly with all supported indicators?**
  - WebSocket streaming includes: RSI(14), EMA(21/50/200), MACD(12,26,9), UTBot(EMA50±3×ATR10), Ichimoku(9/26/52), Quantum Analysis summary (per‑TF and overall), and Currency Strength (8 currencies) computed on latest closed bars.
  - Indicator implementations have unit tests and parity checks; see `tests/test_indicators.py` and `tests/test_parity.py`.

### WebSocket v2

- **Endpoint**: `ws://<host>:<port>/market-v2`
- **Auth**: If `API_TOKEN` is set, include header `X-API-Key: <token>` in the WebSocket handshake (mirrors REST policy).
- **Greeting (server → client on connect)**:

```json
{
  "type": "connected",
  "message": "WebSocket connected successfully",
  "supported_timeframes": ["1M","5M","15M","30M","1H","4H","1D","1W"],
  "notes": ["currency_strength requires timeframe >= 5M"],
  "supported_data_types": ["ticks","indicators"],
  "supported_price_bases": ["last","bid","ask"],
  "indicators": {
    "rsi": {"method": "wilder", "applied_price": "close", "periods": [14]}
  }
}
```

- **Client messages (v2 broadcast-only)**:
  - Ping: `{ "action": "ping" }` → `{ "type": "pong" }`
  - Subscribe/Unsubscribe: `{ "action": "subscribe" }` or `{ "action": "unsubscribe" }` → `{ "type": "info", "message": "v2 broadcast-only: subscribe/unsubscribe ignored" }`
  - Unknown action: `{ "type": "error", "error": "unknown_action" }`

- **Server pushes**:
  - Ticks (about once per second, coalesced):
    ```json
    {
      "type": "ticks",
      "data": [
        {
          "symbol": "EURUSDm",
          "time": 1696229945123,
          "time_iso": "2025-10-02T14:19:05.123Z",
          "bid": 1.06871,
          "ask": 1.06885,
          "volume": 120,
          "daily_change_pct": -0.12
        }
      ]
    }
    ```
  - Indicator update (10s poller; only on new closed bar):
    ```json
    {
      "type": "indicator_update",
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "data": {
        "bar_time": 1696229940000,
        "indicators": {
          "rsi": {"14": 51.23}
        }
      }
    }
    ```
  - Quantum update (computed alongside indicator updates):
  - Currency Strength update (per timeframe; pushed on closed bars only; WS-allowed timeframes ≥ 5M):
    ```json
    {
      "type": "currency_strength_update",
      "timeframe": "5M",
      "data": {
        "bar_time": 1696229940000,
        "strength": {"USD": 23.5, "EUR": -12.2, "GBP": 8.7, "JPY": -31.4, "AUD": 15.9, "CAD": 2.1, "CHF": -5.6, "NZD": -1.0}
      }
    }
    ```
    ```json
    {
      "type": "quantum_update",
      "symbol": "EURUSDm",
      "data": {
        "per_timeframe": {
          "1M": {"buy_percent": 52.1, "sell_percent": 47.9, "final_score": 4.2,
                  "indicators": {"EMA21":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"EMA50":{"signal":"buy","is_new":true,"reason":"Price above EMA"},"EMA200":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"MACD":{"signal":"buy","is_new":false,"reason":"MACD > signal and > 0"},"RSI":{"signal":"neutral","is_new":false,"reason":"RSI in 30-70 range"},"UTBOT":{"signal":"neutral","is_new":false,"reason":"No UTBot trigger"},"ICHIMOKU":{"signal":"sell","is_new":false,"reason":"Price below cloud"}}},
          "5M": {"buy_percent": 61.5, "sell_percent": 38.5, "final_score": 23.1,
                  "indicators": {"EMA21":{"signal":"buy","is_new":true,"reason":"Price above EMA"},"EMA50":{"signal":"buy","is_new":false,"reason":"Price above EMA"},"EMA200":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"MACD":{"signal":"buy","is_new":false,"reason":"MACD > signal and > 0"},"RSI":{"signal":"buy","is_new":true,"reason":"RSI <= 30 (oversold)"},"UTBOT":{"signal":"neutral","is_new":false,"reason":"No UTBot trigger"},"ICHIMOKU":{"signal":"neutral","is_new":false,"reason":"In cloud / mixed; TK/cloud bias"}}},
          "15M": {"buy_percent": 58.0, "sell_percent": 42.0, "final_score": 16.0},
          "30M": {"buy_percent": 49.0, "sell_percent": 51.0, "final_score": -2.0},
          "1H": {"buy_percent": 45.0, "sell_percent": 55.0, "final_score": -10.0},
          "4H": {"buy_percent": 40.0, "sell_percent": 60.0, "final_score": -20.0},
          "1D": {"buy_percent": 50.0, "sell_percent": 50.0, "final_score": 0.0}
        },
        "overall": {
          "scalper": {"buy_percent": 57.3, "sell_percent": 42.7, "final_score": 14.6},
          "swingtrader": {"buy_percent": 47.5, "sell_percent": 52.5, "final_score": -5.0}
        },
        "bar_times": {"5M": 1696229940000, "15M": 1696229700000}
      }
    }
    ```
  - Trending pairs snapshot (startup and hourly):
    ```json
    {
      "type": "trending_pairs",
      "data": {
        "threshold_pct": 0.05,
        "last_updated": "2025-10-06T12:00:00Z",
        "count": 3,
        "pairs": [
          {"symbol": "BTCUSDm", "daily_change_pct": 0.42},
          {"symbol": "XAUUSDm", "daily_change_pct": -0.11},
          {"symbol": "EURUSDm", "daily_change_pct": 0.06}
        ]
      }
    }
    ```
  - Notes:
    - `bar_time` is epoch milliseconds (broker server time).
    - Coverage: RSI/EMA/MACD/UTBot/Ichimoku (closed bars only).

### REST API (Cache-first)

- **Auth**: If `API_TOKEN` is set, include `X-API-Key: <token>` header in requests.

- `GET /api/indicator?indicator=rsi&timeframe=5M&pairs=EURUSDm&pairs=BTCUSDm`
  - Returns latest closed‑bar value for the requested indicator across provided pairs (1–32), served from an in-memory cache.
  - Cache: warm-populated on startup for all allowed symbols/timeframes and updated on each scheduler cycle (closed bars only).
  - If no `pairs`/`symbols` provided, returns for WS‑allowed symbols (capped to 32).
  - Query params:
    - `indicator` (required): `rsi` | `quantum` | `currency_strength`
    - `timeframe` (required): one of `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`.
      - Constraint: for `currency_strength`, minimum timeframe is `5M` (requests with `1M` return error `min_timeframe_5M`).
    - `pairs` (repeatable or CSV): symbols to include. Alias: `symbols`.
  - Response examples:
    ```json
    {"indicator":"rsi","timeframe":"5M","count":2,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":1696229940000,"value":51.23},{"symbol":"BTCUSDm","timeframe":"5M","ts":1696229940000,"value":48.10}]}
    ```
    ```json
    {"indicator":"quantum","timeframe":"5M","count":1,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":null,"quantum":{"per_timeframe":{"5M":{"buy_percent":61.5,"sell_percent":38.5,"final_score":23.1}},"overall":{"scalper":{"buy_percent":57.3,"sell_percent":42.7,"final_score":14.6}}}}]}
    ```
    ```json
    {"indicator":"quantum","timeframe":"5M","count":1,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":null,"quantum":{"per_timeframe":{"1M":{"buy_percent":52.1,"sell_percent":47.9,"final_score":4.2,"indicators":{"EMA21":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"EMA50":{"signal":"buy","is_new":true,"reason":"Price above EMA"},"EMA200":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"MACD":{"signal":"buy","is_new":false,"reason":"MACD > signal and > 0"},"RSI":{"signal":"neutral","is_new":false,"reason":"RSI in 30-70 range"},"UTBOT":{"signal":"neutral","is_new":false,"reason":"No UTBot trigger"},"ICHIMOKU":{"signal":"sell","is_new":false,"reason":"Price below cloud"}},"5M":{"buy_percent":61.5,"sell_percent":38.5,"final_score":23.1,"indicators":{"EMA21":{"signal":"buy","is_new":true,"reason":"Price above EMA"},"EMA50":{"signal":"buy","is_new":false,"reason":"Price above EMA"},"EMA200":{"signal":"neutral","is_new":false,"reason":"Price near EMA"},"MACD":{"signal":"buy","is_new":false,"reason":"MACD > signal and > 0"},"RSI":{"signal":"buy","is_new":true,"reason":"RSI <= 30 (oversold)"},"UTBOT":{"signal":"neutral","is_new":false,"reason":"No UTBot trigger"},"ICHIMOKU":{"signal":"neutral","is_new":false,"reason":"In cloud / mixed; TK/cloud bias"}}}},"overall":{"scalper":{"buy_percent":57.3,"sell_percent":42.7,"final_score":14.6},"swingtrader":{"buy_percent":47.5,"sell_percent":52.5,"final_score":-5.0}},"bar_times":{"5M":1696229940000}}}]}
    ```

- `GET /api/pricing?pairs=EURUSDm&pairs=BTCUSDm`
  - Returns latest cached price snapshot per pair with `bid`, `ask`, `time`, `time_iso`, and `daily_change_pct` (Bid vs D1 reference).
  - If cache miss, falls back to a live MT5 tick for that symbol and backfills the cache.
  - If no `pairs`/`symbols` provided, returns for WS‑allowed symbols (capped to 32).
  - Query params:
    - `pairs` (repeatable or CSV): symbols to include. Alias: `symbols`.
  - Response example:
    ```json
    {
      "count": 2,
      "pairs": [
        {"symbol":"EURUSDm","time":1696229945123,"time_iso":"2025-10-02T14:19:05.123Z","bid":1.06871,"ask":1.06885,"daily_change_pct":-0.12},
        {"symbol":"BTCUSDm","time":1696229946123,"time_iso":"2025-10-02T14:19:06.123Z","bid":27123.5,"ask":27124.1,"daily_change_pct":0.35}
      ]
    }
    ```

- `POST /api/debug/email/send?type={type}&to={email}`
  - Sends a debug email with random content for the specified template type to the given address.
  - Auth: `Authorization: Bearer {DEBUG_EMAIL_API_TOKEN}` (debug-email specific token from `.env`, env var name: `DEBUG_EMAIL_API_TOKEN`).
  - Query params:
    - `type` (string, required): one of `rsi`, `heatmap`, `heatmap_tracker`, `custom_indicator`, `rsi_correlation`, `news_reminder`, `daily_brief`, `currency_strength`, `test`.
      - Aliases: `quantum`, `tracker`, `quantum_tracker` → `heatmap_tracker`; `correlation` → `rsi_correlation`; `cs` → `currency_strength`.
    - `to` (string, required): recipient email address.
  - Curl example:
    ```bash
    curl -X POST \
      -H "Authorization: Bearer $DEBUG_EMAIL_API_TOKEN" \
      "http://localhost:8000/api/debug/email/send?type=rsi&to=user@gmail.com"
    ```
  - Response example:
    ```json
    {"type":"rsi","to":"user@gmail.com","sent":true,"detail":{"pairs":2}}
    ```

- `GET /trending-pairs`
  - Returns the current cached trending pairs snapshot.
  - Threshold is hardcoded to abs(daily_change_pct) ≥ 0.05 for now.
  - Requires `X-API-Key` when `API_TOKEN` is configured.
  - Response example:
    ```json
    {
      "threshold_pct": 0.05,
      "last_updated": "2025-10-06T12:00:12.345678+00:00",
      "count": 2,
      "pairs": [
        {"symbol": "EURUSDm", "daily_change_pct": 0.12},
        {"symbol": "BTCUSDm", "daily_change_pct": 0.41}
      ]
    }
    ```

Note: Tick streaming remains WebSocket-only via `/market-v2`. `/api/pricing` serves cache-first snapshots for convenience.

 

### Recommended client usage

1) On app load, fetch initial data via REST (`/api/indicator`) for selected indicator, symbols, and timeframe.
2) Open WebSocket v2 for live updates. Expect:
   - `ticks` approximately every second (coalesced).
   - `indicator_update` only when a new bar closes (≈ timeframe boundary; detection runs every ~10 seconds).
 
3) Merge live updates into your store. Keep RSI as a closed-bar value; show live price from `ticks`.

### Symbols and timeframes

- Timeframes: fixed set `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`. For `currency_strength`, the minimum timeframe is `5M`.
- Symbols: allowlisted; defaults to all supported RSI symbols (broker-suffixed). Operators can restrict via environment.

### Notes & caveats

- v2 is broadcast-only: `subscribe`/`unsubscribe` are accepted but ignored; no per-client filtering or snapshots.
- Indicator payload coverage: RSI/EMA/MACD/UTBot/Ichimoku; additional `quantum_update` events provide per‑TF and overall Buy/Sell%.
- All indicator values are computed on closed bars only (no intrabar values).

### Quick examples

```javascript
// WebSocket connect
const ws = new WebSocket('ws://localhost:8000/market-v2');
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'ticks') {
    // update live price
  } else if (msg.type === 'indicator_update') {
    // update closed-bar indicators
  }
};
```

```bash
# REST examples
curl -H "X-API-Key: $API_TOKEN" \
  "http://localhost:8000/api/indicator?indicator=rsi&timeframe=1H&pairs=EURUSDm"
```

---

Last updated: 2025-10
