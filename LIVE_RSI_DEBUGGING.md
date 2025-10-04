# Live RSI Debugging

Use this optional telemetry stream when you need to inspect the backend's closed-bar RSI calculations in real time while trading desks or frontend teams validate live behaviour.

## Enabling

Set the `LIVE_RSI_DEBUGGING` environment variable to `true` (defaults to `false`). You can visit this from your deployment configuration, `.env`, or process manager.

```bash
# Example (bash)
export LIVE_RSI_DEBUGGING=true
```

Restart the service after changing the variable so the loader can pick it up.

## What It Does

When enabled, the backend will emit a log entry exactly when a new **closed** 5-minute candle forms for **BTCUSDm**. The message includes:

- Pair label (`BTC/USD`)
- Timeframe (`5M`)
- RSI(14) computed on closed bars only
- Candle timestamp (date + time UTC)
- OHLC values (open, high, low, close)
- Volume, tick volume, and spread

Sample output:

```
🧭 liveRSI BTCUSDm 5M RSIclosed(14)=48.32 | date=2024-09-18 time=12:35:00Z open=26850.10000 high=26870.90000 low=26840.50000 close=26865.40000 volume=132.00 tick_volume=284 spread=21
```

The dedicated compass emoji (`🧭`) allows you to locate these statements quickly inside aggregated logs.

## Notes

- The feed relies on MT5 candles fetched through `get_ohlc_data`. If the terminal is disconnected or delivers stale data, no entries are produced.
- Only **closed** candles trigger the log, so you will not see updates while a candle is still forming.
- Timing: Logs are emitted shortly after each 5M close (sub‑200 ms target), sourced directly from the indicator scheduler/caches. Exact latency depends on OS scheduler and MT5 response.
- The RSI calculation uses the same Wilder smoothing pipeline consumed by all RSI alerts, guaranteeing parity between debug output and production triggers.
- The feature is intentionally scoped to BTCUSDm (5 minutes) to minimise noise. Extend it as needed by adjusting the gating condition in the indicator scheduler within `server.py`.

Disable the variable (or leave it unset) to silence the stream once you finish debugging.
