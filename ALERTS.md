**Overview**
- Supported alerts: RSI Tracker, RSI Correlation Tracker, Quantum Analysis (Heatmap) Tracker, and Quantum Analysis: Custom Indicator Tracker. RSI/Correlation use closed-bar evaluation.
- Delivery channel: Email (IST timestamps). Telegram is out of scope.
- Trigger philosophy: fire on threshold crossings; use per-side cooldown and threshold‑level re‑arm.

**Global Rules**
- Max tracked pairs per user: up to 3.
- Trigger style: crossing into overbought/oversold; not on every bar while in‑zone.
- Closed‑bar evaluation for RSI family: evaluate RSI on the last closed candlestick only (no intrabar/tick evaluation).
- Retrigger policy: once triggered, re‑arm only after leaving the triggerable zone and trigger again only on a fresh crossing back in.
- Timezone for display: Asia/Kolkata.
- System safeguards: rate limit 5 emails/user/hour (overflow → digest), per‑pair concurrency cap, warm‑up for RSI, skip stale TFs (last candle age > 2× TF length).

**Simplified Scope (Current Support)**
- RSI Tracker Alert (single per user)
  - Timeframe: choose exactly one (e.g., `1M`, `5M`, `15M`, `30M`, `1H`, `4H`, `1D`, `1W`).
  - RSI settings: `rsi_period` (5–50), `rsi_overbought` (60–90), `rsi_oversold` (10–40).
  - Pairs: fixed set, backend uses a documented list (no per-alert selection, no env overrides).
  - Behavior: If any pair crosses into overbought/oversold on the closed candle, a trigger is recorded and emailed.
  - Supported trading pairs (MT5-suffixed): `EURUSDm, GBPUSDm, USDJPYm, USDCHFm, AUDUSDm, USDCADm, NZDUSDm, EURGBPm, EURJPYm, EURCHFm, EURAUDm, EURCADm, EURNZDm, GBPJPYm, GBPCHFm, GBPAUDm, GBPCADm, GBPNZDm, AUDJPYm, AUDCHFm, AUDCADm, AUDNZDm, NZDJPYm, NZDCHFm, NZDCADm, CADJPYm, CADCHFm, CHFJPYm, XAUUSDm, XAGUSDm, BTCUSDm, ETHUSDm`.

**System Safeguards**
- Rate limit: max 5 emails/user/hour (overflow → digest).
- Per‑pair concurrency and warm‑up enforced.
- Skip stale TFs (last candle age > 2× TF length).

**Message Structure (email)**
- Title: RSI Alert • {PAIR} ({TF})
- Body: zone entered (Overbought/Oversold), RSI value, price, IST time.
- Footer: Not financial advice.

**Defaults That Work**
- RSI Tracker: timeframe `1H`, period `14`, thresholds OB=70 / OS=30.

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
  - Closed‑bar evaluation: evaluation runs once per closed bar for each pair/timeframe using last‑closed timestamps for both symbols; re‑triggers only after the pair leaves mismatch and then re‑enters on a subsequent closed bar.
  
Fixed correlation pair_keys evaluated:

- Positive: `EURUSDm_GBPUSDm`, `EURUSDm_AUDUSDm`, `EURUSDm_NZDUSDm`, `GBPUSDm_AUDUSDm`, `AUDUSDm_NZDUSDm`, `USDCHFm_USDJPYm`, `XAUUSDm_XAGUSDm`, `XAUUSDm_EURUSDm`, `BTCUSDm_ETHUSDm`, `BTCUSDm_XAUUSDm`
- Negative: `EURUSDm_USDCHFm`, `GBPUSDm_USDCHFm`, `USDJPYm_EURUSDm`, `USDJPYm_GBPUSDm`, `USDCADm_AUDUSDm`, `USDCHFm_AUDUSDm`, `XAUUSDm_USDJPYm`

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

 
