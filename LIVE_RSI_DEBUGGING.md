# Live RSI Debugging

Use this optional telemetry stream when you need to inspect the backend's closed-bar RSI calculations in real time while trading desks or frontend teams validate live behaviour.

## Enabling

Set the `liveRSIDebugging` environment variable to `true` (defaults to `false`). You can visit this from your deployment configuration, `.env`, or process manager.

```bash
# Example (bash)
export liveRSIDebugging=true
```

Restart the service after changing the variable so the loader can pick it up.

## What It Does

When enabled, the backend will emit a log entry every time a new **closed** 1-minute candle forms for **BTC/USD**. The message includes:

- Pair label (`BTC/USD`)
- Timeframe (`1M`)
- RSI(14) computed on closed bars only
- Candle timestamp (date + time UTC)
- OHLC values (open, high, low, close)
- Volume, tick volume, and spread

Sample output:

```
🧭 liveRSI BTC/USD 1 minute RSIclosed(14)=48.32 | date=2024-09-18 time=12:35:00Z open=26850.10000 high=26870.90000 low=26840.50000 close=26865.40000 volume=132.00 tick_volume=284 spread=21
```

The dedicated compass emoji (`🧭`) allows you to locate these statements quickly inside aggregated logs.

## Notes

- The feed relies on MT5 candles fetched through `get_ohlc_data`. If the terminal is disconnected or delivers stale data, no entries are produced.
- Only **closed** candles trigger the log, so you will not see updates while a candle is still forming.
- The RSI calculation uses the same Wilder smoothing pipeline consumed by all RSI alerts, guaranteeing parity between debug output and production triggers.
- The feature is intentionally scoped to BTC/USD (1 minute) to minimise noise. Extend it as needed by adjusting `app/mt5_utils.py`.

Disable the variable (or leave it unset) to silence the stream once you finish debugging.
