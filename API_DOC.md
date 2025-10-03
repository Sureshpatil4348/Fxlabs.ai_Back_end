### API Documentation — WebSocket v2 and REST

This document describes how the frontend should consume market data and indicators from the backend, answers common integration questions, and specifies request/response structures.

### Answers to common questions

- **Mechanism to fetch indicators for different timeframes via WebSocket?**
  - Yes. WebSocket v2 (`/market-v2`) is broadcast-only. The server computes closed-bar indicators on a 10s cadence and pushes `indicator_update` events for all allowed symbols across baseline timeframes: `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`.
  - There is no per-client subscription filtering in v2. Clients receive broadcast updates when a new closed bar is detected.

- **Should the frontend use REST instead?**
  - Use both:
    - WebSocket v2 for live ticks and closed-bar `indicator_update` pushes.
    - REST for initial state via `/api/indicator`.
  - v2 does not send initial OHLC or indicator snapshots on connect; fetch initial state via REST, then merge live pushes.

- **Does it work properly with all supported indicators?**
  - WebSocket streaming currently includes: RSI(14), EMA(21/50/200), MACD(12,26,9) on the latest closed bar for each timeframe.
  - UTBot and Ichimoku are supported by the indicator library and used in alert services, but they are not included in the `indicator_update` payload yet.
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
          "rsi": {"14": 51.23},
          "ema": {"21": 1.06871, "50": 1.06855, "200": 1.06780},
          "macd": {"macd": 0.00012, "signal": 0.00010, "hist": 0.00002}
        }
      }
    }
    ```
  - Notes:
    - `bar_time` is epoch milliseconds (broker server time).
    - Current coverage in this payload: RSI/EMA/MACD. UTBot/Ichimoku are not included yet.

### REST API (Cache-first)

- **Auth**: If `API_TOKEN` is set, include `X-API-Key: <token>` header in requests.

- `GET /api/indicator?indicator=rsi&timeframe=5M&pairs=EURUSDm&pairs=BTCUSDm`
  - Returns latest closed‑bar value for the requested indicator across provided pairs (1–32), served from an in-memory cache.
  - Cache: warm-populated on startup for all allowed symbols/timeframes and updated on each scheduler cycle (closed bars only).
  - If no `pairs`/`symbols` provided, returns for WS‑allowed symbols (capped to 32).
  - Query params:
    - `indicator` (required): `rsi` | `ema` | `macd`
    - `timeframe` (required): one of `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`.
    - `pairs` (repeatable or CSV): symbols to include. Alias: `symbols`.
  - Response examples:
    ```json
    {"indicator":"rsi","timeframe":"5M","count":2,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":1696229940000,"value":51.23},{"symbol":"BTCUSDm","timeframe":"5M","ts":1696229940000,"value":48.10}]}
    ```
    ```json
    {"indicator":"ema","timeframe":"5M","count":1,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":1696229940000,"values":{"21":1.06871,"50":1.06855,"200":1.06780}}]}
    ```
    ```json
    {"indicator":"macd","timeframe":"5M","count":1,"pairs":[{"symbol":"EURUSDm","timeframe":"5M","ts":1696229940000,"values":{"macd":0.00012,"signal":0.00010,"hist":0.00002}}]}
    ```

- `GET /api/tick/{symbol}`
  - Returns the latest tick for a symbol.
  - Response (example):
    ```json
    {
      "symbol": "EURUSDm",
      "time": 1696229945123,
      "time_iso": "2025-10-02T14:19:05.123Z",
      "bid": 1.06871,
      "ask": 1.06885,
      "last": 1.06878,
      "volume": 120
    }
    ```

### Recommended client usage

1) On app load, fetch initial data via REST (`/api/indicator`) for selected indicator, symbols, and timeframe.
2) Open WebSocket v2 for live updates. Expect:
   - `ticks` approximately every second (coalesced).
   - `indicator_update` only when a new bar closes (≈ timeframe boundary; detection runs every ~10 seconds).
3) Merge live updates into your store. Keep RSI as a closed-bar value; show live price from `ticks`.

### Symbols and timeframes

- Timeframes: fixed set `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`.
- Symbols: allowlisted; defaults to all supported RSI symbols (broker-suffixed). Operators can restrict via environment.

### Notes & caveats

- v2 is broadcast-only: `subscribe`/`unsubscribe` are accepted but ignored; no per-client filtering or snapshots.
- Indicator payload coverage is currently RSI/EMA/MACD; UTBot/Ichimoku will appear in alerts and can be computed server-side, but are not published in `indicator_update` yet.
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


