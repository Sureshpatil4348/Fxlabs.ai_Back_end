**Overview**
- Supported alerts: RSI Tracker, RSI Correlation Tracker, Quantum Analysis (Heatmap) Tracker, and Quantum Analysis: Custom Indicator Tracker. RSI/Correlation use closed-bar evaluation.
- Delivery channel: Email (IST timestamps). Telegram is out of scope.
- Trigger philosophy: fire on threshold crossings; use per-side cooldown and threshold‑level re‑arm.

**Global Rules**
- Max tracked pairs per user: up to 3.
- Trigger style: crossing into overbought/oversold; not on every bar while in‑zone.
- Timezone for display: Asia/Kolkata.
- System safeguards: rate limit 5 emails/user/hour (overflow → digest), per‑pair concurrency cap, warm‑up for RSI, skip stale TFs (last candle age > 2× TF length).

**Simplified Scope (Current Support)**
- RSI Tracker Alert (single per user)
  - Timeframe: choose exactly one (e.g., `1M`, `5M`, `15M`, `30M`, `1H`, `4H`, `1D`, `1W`).
  - RSI settings: `rsi_period` (5–50), `rsi_overbought` (60–90), `rsi_oversold` (10–40).
  - Pairs: no selection needed; backend auto-checks all configured pairs.
  - Behavior: If any pair crosses into overbought/oversold on the closed candle, a trigger is recorded and emailed.
  - Default pairs evaluated (when not configured via env): `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `USDCAD`, `AUDUSD`, `NZDUSD`.
  - Environment override: `FX_PAIRS_WHITELIST` (comma-separated) → global pairs for all trackers

**System Safeguards**
- Rate limit: max 5 emails/user/hour (overflow → digest).
- Per‑pair concurrency and warm‑up enforced.
- Skip stale TFs (last candle age > 2× TF length).

**Message Structure (email)**
- Title: RSI Alert • {PAIR} ({TF})
- Body: zone entered (Overbought/Oversold), RSI value, price, IST time.
- Footer: Not financial advice.

**Defaults That Work**
- RSI Tracker: timeframe `1H`, period `14`, thresholds OB=70 / OS=30, cooldown 30m.

**RSI Tracker — Product & Tech Spec**
1) Configuration
  - Single alert per user: timeframe (one), RSI period, OB/OS thresholds.
2) Supabase Schema
  - See `supabase_rsi_tracker_alerts_schema.sql` for `rsi_tracker_alerts` and `rsi_tracker_alert_triggers` (unique `user_id`; RLS for owner).
3) Evaluation Cadence
  - Every minute, server refreshes alert cache, evaluates closed‑bar RSI for subscribed pairs, records triggers, and sends email.
4) RSI Calculation
  - Wilder’s method using broker OHLC; closed‑bar only; warm‑up enforced.
5) Trigger Logic
  - Crossing policy: Overbought (prev < OB and curr ≥ OB), Oversold (prev > OS and curr ≤ OS).
  - Threshold‑level re‑arm per side; per (alert, symbol, timeframe, side) cooldown.
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

### How Alerts Are Evaluated

- Evaluation and trigger insertion are performed by the backend only. The frontend solely manages alert configuration state (CRUD, validation) and must not evaluate thresholds or insert triggers.

## RSI Correlation Tracker Alert (Simplified)

Single per-user alert for the RSI Correlation dashboard. User selects `mode` and timeframe.

- **Mode**: `rsi_threshold` or `real_correlation`
- **Timeframe**: one of `1M,5M,15M,30M,1H,4H,1D,1W` (choose only one)
- **Pairs**: no selection needed; backend auto-checks configured correlation pairs.
- **RSI Threshold**: `rsi_period` (5–50), `rsi_overbought` (60–90), `rsi_oversold` (10–40)
- **Real Correlation**: `correlation_window` (20, 50, 90, 120)
- **Behavior**: Insert a trigger when a correlation pair transitions into a mismatch per rules below.
  
Default correlation pair_keys evaluated (when not configured via env):

- `EURUSD_GBPUSD`, `EURUSD_USDJPY`, `EURUSD_USDCHF`, `EURUSD_USDCAD`, `EURUSD_AUDUSD`, `EURUSD_NZDUSD`
- `GBPUSD_EURUSD`, `GBPUSD_USDJPY`, `GBPUSD_USDCHF`, `GBPUSD_USDCAD`, `GBPUSD_AUDUSD`, `GBPUSD_NZDUSD`
- `USDJPY_EURUSD`, `USDJPY_GBPUSD`, `USDJPY_USDCHF`, `USDJPY_USDCAD`, `USDJPY_AUDUSD`, `USDJPY_NZDUSD`
- `USDCHF_EURUSD`, `USDCHF_GBPUSD`, `USDCHF_USDJPY`, `USDCHF_USDCAD`, `USDCHF_AUDUSD`, `USDCHF_NZDUSD`
- `USDCAD_EURUSD`, `USDCAD_GBPUSD`, `USDCAD_USDJPY`, `USDCAD_USDCHF`, `USDCAD_AUDUSD`, `USDCAD_NZDUSD`
- `AUDUSD_EURUSD`, `AUDUSD_GBPUSD`, `AUDUSD_USDJPY`, `AUDUSD_USDCHF`, `AUDUSD_USDCAD`, `AUDUSD_NZDUSD`
- `NZDUSD_EURUSD`, `NZDUSD_GBPUSD`, `NZDUSD_USDJPY`, `NZDUSD_USDCHF`, `NZDUSD_USDCAD`, `NZDUSD_AUDUSD`

Environment override:
- `FX_PAIRS_WHITELIST` (comma-separated) → build all `A_B` pair_keys for A≠B

Configuration:
- Single alert per user (unique by `user_id`)
- Validate timeframe, mode, RSI bounds, correlation window
- CRUD only on alert config; backend evaluates and inserts triggers

Supabase Schema: `supabase_rsi_correlation_tracker_alerts_schema.sql`
- `rsi_correlation_tracker_alerts` and `rsi_correlation_tracker_alert_triggers` with owner RLS

## Quantum Analysis (Heatmap) Tracker Alert (Simplified)

Single per-user alert for the All-in-One/Quantum Analysis heatmap. Users select up to 3 currency pairs, a mode (trading style), and thresholds. When any selected pair’s Buy% or Sell% crosses its threshold, a trigger is recorded.

- Pairs: up to 3 (e.g., `EURUSD`, `GBPUSD`)
- Mode: `scalper`, `dayTrader`, or `swingTrader`
- Thresholds: `buy_threshold` and `sell_threshold` (0–100)
- Behavior: triggers on upward crossings into threshold for either Buy% or Sell%.

Configuration:
- Single alert per user (unique by `user_id`)
- Validate pairs (≤3), trading style, and thresholds
- CRUD only on alert config; backend evaluates and inserts triggers

Supabase Schema: `supabase_heatmap_tracker_alerts_schema.sql`
- `heatmap_tracker_alerts` and `heatmap_tracker_alert_triggers` with owner RLS

## Quantum Analysis: Custom Indicator Tracker Alert (Simplified)

Single per-user alert targeting one indicator on one timeframe across up to 3 pairs. Notifications are sent when the selected indicator flips its signal (Buy/Sell).

- Pairs: up to 3
- Timeframe: single select (`1M`…`1W`)
- Indicator: one of `EMA21`, `EMA50`, `EMA200`, `MACD`, `RSI`, `UTBOT`, `IchimokuClone`

Configuration:
- Single alert per user (unique by `user_id`)
- Validate pairs (≤3), timeframe, indicator
- CRUD only on alert config; backend evaluates and inserts triggers

Supabase Schema: `supabase_heatmap_indicator_tracker_alerts_schema.sql`
- `heatmap_indicator_tracker_alerts` and `heatmap_indicator_tracker_alert_triggers` with owner RLS

 
