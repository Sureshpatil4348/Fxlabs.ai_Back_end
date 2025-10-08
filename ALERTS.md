**Overview**
- Supported alerts: RSI Tracker, Quantum Analysis (Heatmap) Tracker, Quantum Analysis: Custom Indicator Tracker, and Currency Strength Tracker. RSI uses closed-bar evaluation.
- Delivery channel: Email (IST timestamps). Telegram is out of scope.
- Trigger philosophy: fire on threshold crossings; use per-side cooldown and threshold‑level re‑arm.
 - MT5 data source and closed-bar policy are described in `MT5.md` (see Data Fetch and WebSocket sections).

**Global Rules**
- Max tracked pairs per user: up to 3.
- Trigger style: crossing into overbought/oversold; not on every bar while in‑zone.
- Closed‑bar evaluation for RSI family: evaluate RSI on the last closed candlestick only (no intrabar/tick evaluation). Minimum supported timeframe is 5M.
- Event‑driven alerting: as soon as the indicator scheduler detects a new closed‑bar update and writes to the in‑memory `indicator_cache`, the backend immediately evaluates relevant alerts (RSI Tracker, Indicator Tracker, Heatmap Tracker) using the current alert cache snapshot. This eliminates waiting for the 5‑minute boundary while preserving closed‑bar gating.
- MT5 OHLC fetches include the forming candle flagged with `is_closed=false`. Alert engines strip it automatically for RSI math; client dashboards can keep using it for live rendering without extra filtering.
- Retrigger policy: once triggered, re‑arm only after leaving the triggerable zone and trigger again only on a fresh crossing back in.
- Timezone for display: Asia/Kolkata (tenant-aware for Daily/news: see `DAILY_TZ_NAME`).
- System safeguards: rate limit 5 emails/user/hour (overflow → digest), per‑pair concurrency cap, warm‑up for RSI, skip stale TFs (last candle age > 2× TF length).
  - Startup warm‑up: On server start or first evaluation per key, alerts baseline current state and skip initial triggers for existing in‑zone conditions. Specifically:
    - RSI Tracker: baseline last closed bar per (symbol, timeframe) and require the next new bar for triggers.
    - Heatmap Tracker: initialize armed state per (alert, symbol) from current Buy%/Sell% (disarm sides already above thresholds) and skip the first observation.
    - Indicator Tracker: baseline last signal per (alert, symbol, timeframe, indicator) and skip the first observation.

**Logging**
- All alert logs print to terminal and are also persisted to `logs/<YYYY-MM-DDTHH-mm-ssZ>.log` (UTC server start) with rotation (≈10 MB × 5 files).
- The `logs/` folder is created automatically; you can change location via `LOG_DIR`.
 - To reduce noise, non‑critical diagnostics (e.g., `alert_eval_config`, `alert_eval_start/end`, no‑trigger reasons) are gated behind `ALERT_VERBOSE_LOGS` (default: `false`). Set `export ALERT_VERBOSE_LOGS=true` to see them during debugging.
- Note on `🧭 liveRSI` debugging: when `LIVE_RSI_DEBUGGING=true`, logs are emitted by the indicator scheduler on each M1 closed bar using cache‑aligned RSI values (same source as alerts/WS). The previous helper `app.mt5_utils._maybe_log_live_rsi()` and boundary task have been removed to avoid duplicate math.

**Simplified Scope (Current Support)**
- RSI Tracker Alert (single per user)
  - Timeframe: choose exactly one (e.g., `5M`, `15M`, `30M`, `1H`, `4H`, `1D`, `1W`).
  - RSI settings: period is fixed to 14; configure only `rsi_overbought` (60–90) and `rsi_oversold` (10–40).
  - Pairs: fixed set, backend uses a documented list (no per-alert selection, no env overrides).
  - Behavior: If any pair crosses into overbought/oversold on the closed candle, a trigger is recorded and emailed.
  - Supported trading pairs (MT5-suffixed): `EURUSDm, GBPUSDm, USDJPYm, USDCHFm, AUDUSDm, USDCADm, NZDUSDm, EURGBPm, EURJPYm, EURCHFm, EURAUDm, EURCADm, EURNZDm, GBPJPYm, GBPCHFm, GBPAUDm, GBPCADm, GBPNZDm, AUDJPYm, AUDCHFm, AUDCADm, AUDNZDm, NZDJPYm, NZDCHFm, NZDCADm, CADJPYm, CADCHFm, CHFJPYm, XAUUSDm, XAGUSDm, BTCUSDm, ETHUSDm`.

**Currency Strength Tracker (new)**
- What: Triggers whenever the strongest or weakest fiat currency changes for the configured timeframe.
- Who: One alert per user (single-alert model), delivered via email.
- Timeframe: Choose exactly one (5M, 15M, 30M, 1H, 4H, 1D, 1W). Minimum supported is 5M.
- Universe: Only fiat FX legs are considered: USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD. Non‑fiat symbols (e.g., metals/crypto) are ignored when computing strength.
- Calculation: Closed‑bar ROC on pair closes with log returns aggregated by base/quote contribution and rank‑normalized to a 10–90 scale (see `app/currency_strength.py`).
- Trigger logic: On each closed‑bar evaluation for the selected timeframe, find current strongest and weakest currencies; if either differs from the previously observed winners for this alert, fire exactly once and baseline to the new winners.
- Event cadence: Evaluated on the minute scheduler aligned to 5-minute boundaries (closed‑bar guaranteed). No intrabar/tick evaluation.
- Email: Compact message with timeframe, new strongest/weakest, strength values, and previous winners for context. Cooldown is bypassed for this alert type to ensure every change is sent.

**System Safeguards**
- Per‑pair concurrency and warm‑up enforced.
- Skip stale TFs (last candle age > 2× TF length).
  - Note: Per-user email rate limits and digest have been removed. Alerts are sent immediately when not blocked by service-specific cooldowns (e.g., value-based email cooldown).
- Closed‑bar gating is tracked per alert/user (keyed by `alert_id` along with symbol/timeframe or pair_key). This ensures multiple users with identical configurations are each evaluated every cycle without suppressing later users in the same scheduler tick.

**UT Bot Signal Logic (Parity)**
- Baseline: EMA over closes with length `EMA_LENGTH`.
- Volatility: ATR over highs/lows/closes with length `ATR_LENGTH` and Wilder smoothing.
- Stops: `longStop = baseline − ATR_MULTIPLIER × ATR`, `shortStop = baseline + ATR_MULTIPLIER × ATR`.
- Position: `long` if `close > shortStop`; `short` if `close < longStop`; else `neutral`.
- Flip Detection: within last `K=3` closed bars, if position changes between consecutive bars (ignoring neutral → X) mark `new=true`.
- Signal: `buy` if `close > shortStop` or current position is `long`; `sell` if `close < longStop` or current position is `short`; else `neutral`.
- Confidence: `min(ATR / MIN_ATR_THRESHOLD, 1.0)`; log low-ATR cases but do not hard block.
- Rounding: Return `baseline`, `atr`, `longStop`, `shortStop` rounded to 5 decimals for email/UI consistency.
- Notes:
  - Use only closed bars for evaluation; forming bar is excluded.
  - Parameter names should mirror frontend constants (e.g., `UT_BOT_PARAMETERS`) to keep parity simple.

**Message Structure (email)**
- Title: RSI Alert • {PAIR} ({TF})
- Body: zone entered (Overbought/Oversold), RSI value, price, IST time.
- Footer: The disclaimer appears once at the bottom of the email (not per pair).
- RSI: "Not financial advice. © FxLabs AI"
  - Heatmap/Indicator trackers and Daily: "Education only. © FxLabs AI" (or equivalent wording)

**Defaults That Work**
- RSI Tracker: timeframe `1H`, period `14`, thresholds OB=70 / OS=30.

**RSI Tracker — Product & Tech Spec**
1) Configuration
  - Single alert per user: timeframe (one), RSI period, OB/OS thresholds.
2) Supabase Schema
- See `supabase_rsi_tracker_alerts_schema.sql` for `rsi_tracker_alerts` (unique `user_id`; RLS for owner). Trigger tables are removed.
3) Evaluation Cadence
  - Event‑driven: runs immediately when the indicator scheduler publishes a closed‑bar update for the timeframe. This provides near‑instant triggers after candle close.
  - Boundary alignment: a minute scheduler still runs every 5 minutes as a safety net, but primary evaluation is event‑driven.
4) RSI Calculation
  - Values are sourced from the single source of truth `indicator_cache` (populated by the indicator scheduler using broker OHLC). Closed‑bar only; warm‑up enforced. No per-alert recomputation.
5) Trigger Logic
  - Crossing policy: Overbought (prev < OB and curr ≥ OB), Oversold (prev > OS and curr ≤ OS), evaluated on closed bars only.
  - Threshold‑level re‑arm per side. No additional per-pair cooldown applied for RSI Tracker.
6) Alert Content
  - Email Subject: `RSI Alert - <alert_name>`; includes per‑pair summary (zone, RSI value, price, IST time).
7) Example Config (JSON)
```json
{
  "timeframe": "1H",
  "rsiPeriod": 14,
  "rsiOverbought": 70,
  "rsiOversold": 30
}
```

**Supabase — Table Schemas (Canonical)**
- See `supabase_rsi_tracker_alerts_schema.sql`.

> Note on multitenancy: All alert trigger inserts now use tenant-aware Supabase via `app/config.py` and `app/tenancy.py`. Use `python fxlabs-server.py` or `python hextech-server.py` to select the tenant; set the corresponding credentials in `.env`.

### How Alerts Are Evaluated

- Evaluation and trigger insertion are performed by the backend only. The frontend solely manages alert configuration state (CRUD, validation) and must not evaluate thresholds or insert triggers.

 

## Quantum Analysis (Heatmap) Tracker Alert

Single per-user alert for the All-in-One/Quantum Analysis heatmap. Users select up to 3 currency pairs, a mode (trading style), and thresholds. When any selected pair’s Buy% or Sell% crosses its threshold, a trigger is recorded.

- Pairs: up to 3 (e.g., `EURUSD`, `GBPUSD`)
- Mode: `scalper` or `swingTrader`
- Thresholds: `buy_threshold` and `sell_threshold` (0–100). Internally we compute the style‑weighted Final Score and convert to Buy%/Sell% per the Calculations Reference.
- Behavior: triggers on upward crossings into threshold for either Buy% or Sell%.

Configuration:
- Single alert per user (unique by `user_id`)
- Validate pairs (≤3), trading style, and thresholds
- CRUD only on alert config; backend evaluates triggers; no DB trigger table

Supabase Schema: `supabase_heatmap_tracker_alerts_schema.sql`
- `heatmap_tracker_alerts` only (trigger tables removed)

## Quantum Analysis: Custom Indicator Tracker Alert (Simplified)

Single per-user alert targeting one indicator on one timeframe across up to 3 pairs. Notifications are sent when the selected indicator flips its signal (Buy/Sell).

- Pairs: up to 3
- Timeframe: single select (`1M`…`1W`)
- Indicator: one of `EMA21`, `EMA50`, `EMA200`, `MACD`, `RSI`, `UTBOT`, `IchimokuClone`

Configuration:
- Single alert per user (unique by `user_id`)
- Validate pairs (≤3), timeframe, indicator
- CRUD only on alert config; backend evaluates triggers; no DB trigger table

Supabase Schema: `supabase_heatmap_indicator_tracker_alerts_schema.sql`
- `heatmap_indicator_tracker_alerts` only (trigger tables removed)

Implementation details (backend alignment with Calculations Reference):
 - Cache and centralization:
   - RSI, EMA(21/50/200), MACD(12,26,9) values are sourced from the single source of truth `indicator_cache` (closed-bar only).
   - UTBot and Ichimoku values are computed via `app.indicators` over closed OHLC; no ad-hoc math in services.
- Heatmap/Quantum Buy%/Sell% aggregation:
  - Indicators per timeframe: EMA21, EMA50, EMA200, MACD(12,26,9), RSI(14), UTBot(EMA50 ± 3×ATR10), Ichimoku Clone (9/26/52).
  - New‑signal boost: detection over last K=3 closed candles per indicator (close/EMA cross, MACD cross, RSI 50/30/70 crossings, UTBot flip, Ichimoku TK cross/cloud breakout).
  - Quiet‑market safety: compute ATR10 series; if current ATR is below the 5th percentile of the last 200 values, halve MACD and UTBot cell scores on that timeframe.
  - Per‑cell scoring: buy=+1, sell=−1, neutral=0; add ±0.25 on new signals; clamp in [−1.25,+1.25].
  - Weights: trading‑style timeframe weights (scalper: 5M/15M/30M/1H/4H; swingTrader: 30M/1H/4H/1D) and equal indicator weights by default.
  - Aggregation: Raw = Σ_tf Σ_ind S(tf,ind)×W_tf×W_ind; Final = 100×(Raw/1.25); Buy%=(Final+100)/2; Sell%=100−Buy%.
  - Re‑arm policy: Buy side rearms after Buy% drops below (buy_threshold−5); Sell side rearms after Buy% rises above (sell_threshold+5). Triggers fire on crossing into thresholds.
- Indicator Tracker signals now derive from real OHLC:
  - EMA21/EMA50/EMA200: BUY on close crossing above EMA; SELL on crossing below.
  - RSI: BUY on RSI(14) crossing up through 50; SELL on crossing down through 50.
  - Unknown indicators resolve to neutral (no trigger).

Why you might not see triggers yet
- No active alerts: Ensure rows exist in Supabase for `heatmap_tracker_alerts` and `heatmap_indicator_tracker_alerts` with `is_active=true` and non-empty `pairs` (max 3).
- Thresholds too strict: For Heatmap, start with Buy≥70 / Sell≤30. With multi‑indicator aggregation, Final can concentrate near neutral on choppy days, especially in swingTrader style.
- Arm/disarm gating: Buy disarms after a BUY trigger and rearms once RSI < (buy_threshold−5); Sell disarms after a SELL trigger and rearms once RSI > (sell_threshold+5).
- Closed‑bar cadence: Evaluation is event‑driven (on each closed bar) with a 5‑minute safety scheduler; low TFs see more opportunities.

 

## News Reminder Alerts
Automatic email 5 minutes before each scheduled high‑impact news item


### ⏰ News Reminder (5 Minutes Before)

- What: Sends an email with subject "News reminder" to all active users 5 minutes before each upcoming news event found in the local news cache.
  - Impact filter: Only items with AI‑normalized `impact == "high"` qualify. Medium/low impact items are ignored.
- Who: All user emails fetched from Supabase Auth (`auth.users`) using the service role key. This is the single source of truth for news reminders and does not depend on per‑product alert tables.
  - Primary source: `GET {SUPABASE_URL}/auth/v1/admin/users` with `Authorization: Bearer {SUPABASE_SERVICE_KEY}`
  - Pagination: `page`, `per_page` (defaults: 1..N, 1000 per page)
  - Email extraction: Primary `email`, fallback to `user_metadata.email/email_address/preferred_email`, and `identities[].email`/`identities[].identity_data.email` for OAuth providers
  - Fallback: If Auth returns no emails, falls back to union of alert tables (`rsi_tracker_alerts`, `rsi_correlation_tracker_alerts`, `heatmap_tracker_alerts`, `heatmap_indicator_tracker_alerts`)
- When: A dedicated 1-minute scheduler runs in `server.py` and calls `app.news.check_and_send_news_reminders()`.
  - The function filters the due window to high‑impact items only.
- How it avoids duplicates: Each `NewsAnalysis` item has a boolean `reminder_sent`. Once sent, the item is flagged and the cache is persisted to disk, preventing repeats across restarts.
- Template: Minimal, mobile-friendly HTML wrapped with the unified green header (`FXLabs • News • <date/time>`) and a single common disclaimer footer.
  - Fields: `event_title`, `event_time_local` (IST by default), `impact`, `previous`, `forecast`, `expected` (shown as `-` pre-release), `bias` (from AI effect → Bullish/Bearish/Neutral).
- Logging: Uses human-readable logs via `app/alert_logging.py` with events:
  - Auth fetch: `news_auth_fetch_start`, `news_auth_fetch_page`, `news_auth_fetch_page_emails` (debug), `news_auth_fetch_done`
  - Fallback: `news_users_fetch_fallback_alert_tables`
  - Send: `news_auth_emails` (full CSV), `news_reminder_recipients`, `news_reminder_completed`
- Requirements: SendGrid configured (`SENDGRID_API_KEY`, `FROM_EMAIL`, `FROM_NAME`) and Supabase (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`). If either is missing, the scheduler logs and skips sending.

## Daily Morning Brief
Automated daily email to all users at a configurable local time

- What: A daily brief sent to all users at 09:00 IST containing:
  - Core signals for EUR/USD, XAU/USD, BTC/USD from the All‑in‑One (Quantum) model
  - RSI(14) on 4H: lists of pairs currently Oversold (≤30) and Overbought (≥70)
  - Today's high/medium‑impact news from the local news cache (IST day)
- Who: All user emails fetched from Supabase Auth (`auth.users`) using the service role key. This is the single source of truth for daily emails and does not depend on per‑product alert tables.
  - Primary source: `GET {SUPABASE_URL}/auth/v1/admin/users` with `Authorization: Bearer {SUPABASE_SERVICE_KEY}`
  - Pagination: `page`, `per_page` (defaults: 1..N, 1000 per page)
  - Email extraction: Primary `email`, fallback to `user_metadata.email/email_address/preferred_email`, and `identities[].email`/`identities[].identity_data.email` for OAuth providers
  - The code automatically paginates and deduplicates emails
- When: A daily scheduler computes the next configured local send time and sleeps until then; after sending, it schedules for the next day.
- Config:
  - `DAILY_TZ_NAME` (default `Asia/Kolkata`) — IANA timezone used for scheduling and display label ("IST" when `Asia/Kolkata`).
  - `DAILY_SEND_LOCAL_TIME` (default `09:00`) — local time in `HH:MM` or `HH:MM:SS`.
  - The email header shows the same time label (e.g., `IST 09:00`).
- Data sources:
  - Core signals: reuse Heatmap/Quantum `_compute_buy_sell_percent(symbol, style)` with `scalper` style for EURUSDm, XAUUSDm, BTCUSDm
  - RSI(14) 4H: uses real MT5 OHLC via `get_ohlc_data` and computes RSI locally
  - News: filters `global_news_cache` for items with IST date == today and impact in {high, medium}
- Template: Responsive table layout; badges (BUY=#0CCC7C, SELL=#E5494D); simple lists for RSI and a compact news table.
- Logging: Uses human-readable logs via `app/alert_logging.py` with events:
  - Auth fetch: `daily_auth_fetch_start`, `daily_auth_fetch_page`, `daily_auth_fetch_page_emails` (debug), `daily_auth_fetch_done`
  - Send: `daily_auth_emails` (full CSV), `daily_send_batch`, `daily_completed`
  - Scheduler: `daily_sleep_until`, `daily_build_start`, `daily_build_done`, with error events on failures

Email HTML structure example (simplified):

```html
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FxLabs • News Reminder</title></head>
<body style="margin:0;background:#F5F7FB;"> ... </body></html>
```

### Common Email Header (All alerts)

- Color: `#07c05c`
- Layout: `[FxLabs logo] FXLabs • <Alert Type> • <Local Date IST> • <Local Time IST>`
  - The time part is rendered in a smaller font size.
  - Logo uses the white SVG mark embedded inline for email compatibility.
- Timezone: Defaults to `Asia/Kolkata` (IST). For Daily emails, the header shows the configured time label (e.g., `IST 09:00`).
## Alerts Cache — Categories Summary

After each alert cache refresh, the server logs a categories summary and a full list of alerts grouped by category. Example console output:

```
🔄 Refreshing alert cache...
✅ Alert cache refreshed: 2 users, 8 total alerts
📚 Alerts by category (post-refresh):
  • rsi_tracker: 2
     - id=... | name=RSI Tracker Alert | user=test@asoasis.tech
     - id=... | name=RSI Tracker Alert | user=demo@example.com
  • heatmap_tracker: 3
     - id=... | name=Heatmap Tracker Alert | user=...
  • heatmap_indicator_tracker: 2
     - id=... | name=Indicator Tracker Alert | user=...
 
```

Additionally:

**Troubleshooting: SendGrid 403 Forbidden (intermittent)**
- Symptom: Logs show `❌ Error sending ... email: HTTP Error 403: Forbidden` for some sends but not others.
- Likely causes and fixes:
  - Sender identity mismatch: Ensure the tenant-specific `FROM_EMAIL` matches a verified Single Sender or an authenticated domain in SendGrid. No default is used; set `FXLABS_FROM_EMAIL` or `HEXTECH_FROM_EMAIL` accordingly.
  - API key scopes: Confirm the tenant-specific API key (`FXLABS_SENDGRID_API_KEY` or `HEXTECH_SENDGRID_API_KEY`) includes `Mail Send` permission. Regenerate the key if needed.
  - IP Access Management: If enabled, whitelist the server IP to avoid 403.
  - Region: If your account is EU-only, ensure your environment targets the EU endpoint (contact SendGrid support/docs for region setup). 
- Why it appears intermittent:
  - Different shells/processes may load different env files. Ensure you set the correct tenant-specific variables (`FXLABS_*` for FXLabs or `HEXTECH_*` for HexTech) in the active environment. No fallback defaults are used.
- What the app logs on failure:
  - Status code, trimmed response body, masked API key, and from/to addresses to aid diagnosis without leaking secrets.
- A structured log line with per-category counts is emitted as `app.alert_cache | alert_cache_categories` for observability.
- For each alert in the listing, a concise config snapshot is printed per type:
  - RSI Tracker: `tf`, `period`, `ob` (overbought), `os` (oversold)
  - RSI Correlation Tracker: `tf`, `mode`, `period`, `ob`, `os`, `window`
  - Heatmap Tracker: `style`, `buy_threshold`, `sell_threshold`, `pairs`
  - Indicator Tracker: `indicator`, `tf`, `pairs`

### REST: Alerts by Category

- Endpoint: `GET /api/alerts/by-category`
- Auth: `X-API-Key` (same as other alert endpoints)
- Response:

```json
{
  "total_alerts": 8,
  "last_refresh": "2025-09-30T15:50:35+00:00",
  "is_refreshing": false,
  "categories": {
    "rsi_tracker": [ { "id": "...", "alert_name": "RSI Tracker Alert", ... } ],
    "heatmap_tracker": [ ... ],
    "heatmap_indicator_tracker": [ ... ],
    "rsi_correlation_tracker": [ ... ]
  }
}
```

Notes:
- The `categories` lists reuse the canonical alert objects as cached per user; fields vary by alert type (e.g., `timeframe` for RSI, `pairs` for Heatmap).
- The categories summary is also printed to the console after refresh for quick admin inspection.

## Evaluation Logs (Debug)
At DEBUG level, evaluators provide concise reasons when a trigger does not fire, clarifying how each alert was processed:

- RSI Tracker: `rsi_insufficient_data`, `rsi_rearm_overbought`, `rsi_rearm_oversold`, `rsi_no_trigger` (reason and RSI values vs thresholds)
- RSI Correlation: `corr_no_mismatch`, `corr_persisting_mismatch`
- Heatmap Tracker: `heatmap_eval`, `heatmap_no_trigger` (Buy%/Sell%, thresholds, armed flags)
- Indicator Tracker: `indicator_signal`, `indicator_no_trigger` (neutral or no flip)

Set `LOG_LEVEL=DEBUG` to enable.
