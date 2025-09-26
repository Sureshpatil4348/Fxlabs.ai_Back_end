**Overview**
- Single alert: RSI Tracker Alert (closed-bar only). Heatmap and RSI Correlation alerts have been removed.
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
  - Behavior: If any subscribed pair crosses into overbought/oversold on the closed candle, a trigger is recorded and emailed.

**Minimal UI**
- Open from the bell icon in `src/components/RSIOverboughtOversoldTracker.js`.
- Config component: `src/components/RSITrackerAlertConfig.jsx`.
- Fields: `timeframe` (single), `rsiPeriod` (5–50), `rsiOverbought` (60–90), `rsiOversold` (10–40). Delete removes; save upserts the single alert.

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
1) Configuration (UI)
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
  - Email Subject: `RSI Alert - <alert_name>`; HTML per‑pair card matching the template below.
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

## RSI Correlation Tracker Alert (Simplified)

Single per-user alert for the RSI Correlation dashboard. User selects `mode` and timeframe.

- **Mode**: `rsi_threshold` or `real_correlation`
- **Timeframe**: one of `1M,5M,15M,30M,1H,4H,1D,1W`
- **RSI Threshold**: `rsi_period` (5–50), `rsi_overbought` (60–90), `rsi_oversold` (10–40)
- **Real Correlation**: `correlation_window` (20, 50, 90, 120)
- **Behavior**: Insert a trigger when a correlation pair transitions into a mismatch per rules below.

UI: `src/components/RSICorrelationTrackerAlertConfig.jsx` (opened from `src/components/RSICorrelationDashboard.js`).

Client Evaluation: `src/store/useRSICorrelationStore.js`
- Threshold mode:
  - Positive pairs: one ≥ OB and other ≤ OS
  - Negative pairs: both ≥ OB or both ≤ OS
  - Trigger on transitions into mismatch (prev != mismatch AND next == mismatch)
- Real correlation mode:
  - Positive pairs: correlation < +0.25 → mismatch
  - Negative pairs: correlation > -0.15 → mismatch
  - Trigger on transitions into mismatch

Service: `src/services/rsiCorrelationTrackerAlertService.js`
- Single alert per user (upsert by `user_id`)
- Validate timeframe, mode, RSI bounds, correlation window
- CRUD + `createTrigger({ alertId, pairKey, timeframe, mode, triggerType, value })`

Supabase Schema: `supabase_rsi_correlation_tracker_alerts_schema.sql`
- `rsi_correlation_tracker_alerts` and `rsi_correlation_tracker_alert_triggers` with owner RLS


**Frontend — Exact Implementation Requirements**
- Component: `src/components/RSITrackerAlertConfig.jsx`
- Store: `src/store/useRSITrackerStore.js`
- Service: `src/services/rsiTrackerAlertService.js` (CRUD + `createTrigger`)

Template: RSI tracker alert (html):

<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FxLabs • RSI Alert</title>
</head>
<body style="margin:0;background:#F5F7FB;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F5F7FB;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;">
  <tr><td style="padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;">RSI Alert • {{pair}} ({{timeframe}})</td></tr>
  <tr><td style="padding:20px;">
    <div style="margin-bottom:10px;">RSI has entered <strong>{{zone}}</strong>.</div>
    <div style="font-size:14px;line-height:1.6">
      <strong>Current RSI:</strong> {{rsi}}<br>
      <strong>Price:</strong> {{price}}<br>
      <strong>Time:</strong> {{ts_local}}
    </div>
    <div style="margin-top:16px;padding:12px;border-radius:10px;background:#F9FAFB;color:#374151;font-size:13px;">
      Heads-up: Oversold/Overbought readings can precede reversals or trend continuation. Combine with your plan.
    </div>
  </td></tr>
  <tr><td style="padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;">Not financial advice. © FxLabs AI</td></tr>
</table>
</td></tr></table>
</body></html>
