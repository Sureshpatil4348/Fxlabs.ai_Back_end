### API Documentation — WebSocket v2 and REST

This document describes how the frontend should consume market data and indicators from the backend, answers common integration questions, and specifies request/response structures.

### Answers to common questions

- **Mechanism to fetch indicators for different timeframes via WebSocket?**
  - Yes. WebSocket v2 (`/market-v2`) is broadcast-only. The server computes closed-bar indicators on a 10s cadence and pushes `indicator_update` events for all allowed symbols across baseline timeframes: `1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W`. It also broadcasts `currency_strength_update` snapshots over WebSocket only on closed bars and only for supported (WS-allowed) timeframes, using closed-candle ROC aggregation for the 8 fiat currencies and normalizing to a −100..100 scale (0 = neutral). Note: Currency Strength enforces a minimum timeframe of `5M` (no `1M`).
  - Update: Indicator WS pushes are consolidated by timeframe. Each cycle emits one `indicator_updates` message per timeframe with an array of all symbols updated in that cycle.
  - There is no per-client subscription filtering in v2. Clients receive broadcast updates when a new closed bar is detected.

- **Should the frontend use REST instead?**
  - Use both:
    - WebSocket v2 for live ticks, closed-bar `indicator_updates`, and closed-bar `currency_strength_update` pushes (only for WS-allowed timeframes).
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
  "supported_data_types": ["ticks","indicators","ohlc"],
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
  - Ticks (about every 500ms; one message per scan with latest for all pairs, bid-only):
    ```json
    {
      "type": "ticks",
      "data": [
        {
          "symbol": "EURUSDm",
          "time": 1696229945123,
          "time_iso": "2025-10-02T14:19:05.123Z",
          "bid": 1.06871,
          "daily_change_pct": -0.12,
          "daily_change": -0.00129
        },
        {
          "symbol": "BTCUSDm",
          "time": 1696229946123,
          "time_iso": "2025-10-02T14:19:06.123Z",
          "bid": 27123.5,
          "daily_change_pct": 0.35,
          "daily_change": 95.2
        }
      ]
    }
    ```
  - Latency and scalability notes:
    - Behavior: a single global TickHub polls MT5 ~2 Hz (≈500ms), coalesces latest ticks for all allowed symbols, pre‑serializes one payload, and broadcasts it to all connected v2 clients. Clients do not perform MT5 calls.
    - Results: eliminates per‑client duplication, reduces thread pool contention, and lowers jitter under fan‑out. Metrics still record per‑client send success/failure and item counts.
    - Higher scale: if needed in the future, TickHub can be moved to a dedicated process with IPC/pub‑sub to support multiple web workers.
  - Tick calculation and push pipeline (implementation summary):
    - Source: MT5 `symbol_info_tick` per allowed symbol; converted to `Tick` via `app.mt5_utils._to_tick`.
    - Frequency and batching: a single loop sends coalesced updates about every 500ms (~2 Hz). Within each scan, only symbols with a new tick timestamp are included. One `{"type":"ticks","data":[...]}` message per scan.
    - Timestamp semantics: `time` is the broker-provided epoch milliseconds (`time_msc` fallback to `time*1000`), and `time_iso` is derived from it in UTC. Timestamps are per-item; there is no outer batch-level timestamp in v2.
    - Fields: payload is bid-only for UI (`symbol, time, time_iso, bid`). Server may compute and include `daily_change` and `daily_change_pct`.
    - Daily change math (Bid basis):
      - Reference: if the latest D1 bar is for today (UTC), use that bar's open (Bid); else use the previous D1 bar's close (Bid). See `app.mt5_utils._get_d1_reference_bid`.
      - Values: `daily_change = bid_now − D1_reference`, `daily_change_pct = 100 * (bid_now − D1_reference) / D1_reference`.
    - Caching/side-effects: latest per-symbol snapshot is stored in `app.price_cache` for REST `/api/pricing` reads; OHLC caches are refreshed internally for baseline timeframes but OHLC is not streamed in v2.
    - Backpressure/robustness: send uses best-effort non-blocking writes; metrics counters track `ok_ticks`, `fail_ticks`, and total items.
  - Indicator updates (10s poller; consolidated by timeframe; only on new closed bars):
    ```json
    {
      "type": "indicator_updates",
      "timeframe": "5M",
      "data": [
        { "symbol": "EURUSDm", "bar_time": 1696229940000, "indicators": { "rsi": {"14": 51.23} } },
        { "symbol": "BTCUSDm",  "bar_time": 1696229940000, "indicators": { "rsi": {"14": 48.10} } }
      ]
    }
    ```
  - OHLC updates (10s poller; consolidated by timeframe; on candle close for all symbols):
    ```json
    {
      "type": "ohlc_updates",
      "timeframe": "5M",
      "data": [
        {
          "symbol": "EURUSDm",
          "bar_time": 1696229940000,
          "ohlc": {
            "time_iso": "2025-10-02T14:19:00Z",
            "open": 1.06791,
            "high": 1.06871,
            "low": 1.06750,
            "close": 1.06810,
            "volume": 1234,
            "tick_volume": 5678,
            "spread": 12
          }
        },
        {
          "symbol": "BTCUSDm",
          "bar_time": 1696229940000,
          "ohlc": { "open": 27050.0, "high": 27150.0, "low": 27000.0, "close": 27123.5 }
        }
      ]
    }
    ```
  - Other broadcasts
    - All server push types (ticks, indicator_updates, currency_strength_update, quantum_update, trending_pairs) are produced by single producers and broadcast using a pre‑serialized payload for low latency and scalability. Broadcast writes are parallelized across clients to minimize backpressure. Clients do not perform MT5 calls.
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

- `GET /api/ohlc?symbol=EURUSDm&timeframe=5M&limit=100&before=1696230060000`
  - Returns OHLC bars for a single symbol and timeframe using cursor (keyset) pagination.
  - Auth: `X-API-Key: {API_TOKEN}` when `API_TOKEN` is configured.
  - Query params:
    - `symbol` (string, required): e.g., `EURUSDm`.
    - `timeframe` (string, required): one of `1M,5M,15M,30M,1H,4H,1D,1W`.
    - `limit` (int, optional, default `100`, max `1000`): number of bars to return.
    - `before` (int, optional): epoch milliseconds; return bars strictly older than this bar time (page older).
    - `after` (int, optional): epoch milliseconds; return bars strictly newer than this bar time (page newer).
      - Provide either `before` or `after`, not both. If neither is provided, the most recent `limit` bars are returned.
  - Notes:
    - Bars are returned in ascending time order for stable client-side processing.
    - Each bar includes `is_closed` indicating whether the candle is closed at response time.
    - Symbols may be filtered to an allowlist when configured.
    - Response includes `next_before` and `prev_after` cursors (raw timestamps) to request the next/previous slice without duplication, even when new candles form.
    - Meaning of "count": number of OHLC records in this response (length of `bars`).
  - Response example (most recent slice):
    ```json
    {
      "symbol": "EURUSDm",
      "timeframe": "5M",
      "limit": 3,
      "count": 3,
      "before": null,
      "after": null,
      "next_before": 1696229820000,
      "prev_after": 1696230060000,
      "bars": [
        {"symbol":"EURUSDm","timeframe":"5M","time":1696229820000,"time_iso":"2025-10-02T14:17:00+00:00","open":1.06870,"high":1.06890,"low":1.06860,"close":1.06880,"volume":120,"tick_volume":120,"spread":12,"openBid":1.06864,"highBid":1.06884,"lowBid":1.06854,"closeBid":1.06874,"openAsk":1.06876,"highAsk":1.06896,"lowAsk":1.06866,"closeAsk":1.06886,"is_closed":true},
        {"symbol":"EURUSDm","timeframe":"5M","time":1696229940000,"time_iso":"2025-10-02T14:19:00+00:00","open":1.06880,"high":1.06900,"low":1.06870,"close":1.06895,"volume":98,"tick_volume":98,"spread":12,"openBid":1.06874,"highBid":1.06894,"lowBid":1.06864,"closeBid":1.06889,"openAsk":1.06886,"highAsk":1.06906,"lowAsk":1.06876,"closeAsk":1.06901,"is_closed":true},
        {"symbol":"EURUSDm","timeframe":"5M","time":1696230060000,"time_iso":"2025-10-02T14:21:00+00:00","open":1.06895,"high":1.06905,"low":1.06880,"close":1.06892,"volume":45,"tick_volume":45,"spread":12,"openBid":1.06889,"highBid":1.06899,"lowBid":1.06874,"closeBid":1.06886,"openAsk":1.06901,"highAsk":1.06911,"lowAsk":1.06886,"closeAsk":1.06898,"is_closed":false}
      ]
    }
    ```
  - Paging older (use `next_before` from previous response):
    ```
    GET /api/ohlc?symbol=EURUSDm&timeframe=5M&limit=100&before=<next_before>
    ```
  - Paging newer (use `prev_after` from previous response):
    ```
    GET /api/ohlc?symbol=EURUSDm&timeframe=5M&limit=100&after=<prev_after>
    ```

#### How OHLC data is fetched (backend)
- Source: MetaTrader 5 via Python bindings. The server calls `mt5.copy_rates_from_pos(symbol, timeframe, 0, N)` to obtain the latest N bars for the requested timeframe.
- Async wrapper: `server.py` uses `_get_ohlc_data_async(...)` to run the blocking MT5 call off the event loop (`asyncio.to_thread`) to keep FastAPI responsive.
- Conversion: Each MT5 rate is converted to an internal OHLC model in `app/mt5_utils._to_ohlc(...)`, which sets:
  - `time` (ms) and `time_iso` (UTC) from the broker’s timestamp
  - `is_closed` by comparing the bar start time plus timeframe length against current time
  - Optional Bid/Ask-parallel fields (`openBid`/`openAsk`, etc.) when available, else falls back to OHLC
- Symbol handling: Symbols are normalized with `canonicalize_symbol(...)` and validated via `ensure_symbol_selected(...)`. An allowlist may be enforced; unknown/blocked symbols return an error.
- Cursor selection: The endpoint fetches a bounded window (`limit*5` clamped to `[1000, 5000]`), sorts ascending by time, and then applies keyset filters:
  - `before=<t>` → return bars with `time < t` (older)
  - `after=<t>` → return bars with `time > t` (newer)
  - Without a cursor → most recent `limit` bars
- Response cursors: `next_before` is the oldest bar’s time in the returned slice (use to page older). `prev_after` is the newest bar’s time (use to page newer).

#### Limitations & notes
- MT5-bound window: There is no server-side historical cursor in MT5 for arbitrary deep history in this endpoint. The server fetches a recent window (up to 5000 bars per call) and keyset-slices in-memory. Very old data may require multiple requests stepping with `before`.
- Live/forming candle: The final element from MT5 can be a forming candle; its `is_closed` will be `false`. Consumers should treat indicators as closed-bar only (already enforced in indicator paths).
- Time semantics: `time` is broker server time; `time_iso` is UTC. `is_closed` is computed relative to the server’s current clock and timeframe. Significant system clock drift could affect close detection.
- Field availability: Bid/Ask-parallel fields depend on broker data. When not present, values fall back to OHLC or may be `null`.
- Throughput: Each call performs an MT5 fetch. There is a separate in-memory OHLC cache used by WebSocket close-boundary updates, but `/api/ohlc` fetches directly to ensure freshness.
- Limits: `limit` ≤ 1000. Internal fetch cap ≤ 5000 per request. Use cursors to iterate.

- `POST /api/debug/email/send?type={type}&to={email}`
  - Sends a debug email with random content for the specified template type to the given address.
  - Auth: `Authorization: Bearer {DEBUG_API_TOKEN}` (debug-specific token from `.env`, env var name: `DEBUG_API_TOKEN`; applies to all `/api/debug/*`).
  - Query params:
    - `type` (string, required): one of `rsi`, `heatmap`, `heatmap_tracker`, `custom_indicator`, `rsi_correlation`, `news_reminder`, `daily_brief`, `currency_strength`, `test`.
      - Aliases: `quantum`, `tracker`, `quantum_tracker` → `heatmap_tracker`; `correlation` → `rsi_correlation`; `cs` → `currency_strength`.
    - `to` (string, required): recipient email address.
  - Behavior:
    - Returns HTTP 200 with a JSON body that includes `sent: true|false`. A value of `false` indicates the provider rejected the send (e.g., SendGrid 400/403). The endpoint does not mirror the provider's HTTP status.
  - Curl example:
    ```bash
    curl -X POST \
      -H "Authorization: Bearer $DEBUG_API_TOKEN" \
      "http://localhost:8000/api/debug/email/send?type=rsi&to=user@gmail.com"
    ```
  - Notes:
    - All recipient domains are allowed; only email format is validated. Invalid format returns `400 {"detail":"invalid_recipient"}`. Debug sends are rate‑limited per token.
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
   - `ticks` approximately every second per scan (one message containing latest ticks for all allowed pairs; bid-only).
   - `indicator_updates` only when a new bar closes (≈ timeframe boundary; detection runs every ~10 seconds); one message per timeframe with an array of symbols.
 
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
  } else if (msg.type === 'indicator_updates') {
    // update closed-bar indicators (batch for this timeframe)
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
