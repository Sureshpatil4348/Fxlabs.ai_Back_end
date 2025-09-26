**Overview**
- Unified alerts spec covering Heat Map Threshold (Type A), Indicator Flip (Type B), and RSI Overbought/Oversold (OB/OS).
- Delivery channels: Email and Telegram. All timestamps display in Asia/Kolkata (IST).
- Trigger philosophy: fire on crossings or regime flips (not every bar while the condition remains true); use cooldowns and hysteresis to reduce noise.

**Global Rules**
- Max tracked pairs per user: up to 3.
- Delivery channels: Email / Telegram.
- Trigger style: crossing into the condition (prevents spam). Fires only when the metric crosses a threshold or a signal flips; not on every bar while in-zone.
- Timezone for display: Asia/Kolkata.
- System safeguards (apply to all alerts): rate limit 5 alerts/user/hour (overflow batched to a digest), per‑pair concurrency cap, warm‑up for indicators, skip stale TFs (last candle age > 2× TF length).

**Type A — Buy Now % Threshold (multi‑pair)**
- Intent: Alert when any chosen pair becomes strong enough to act.
- Inputs
  - Pairs: 1–3 symbols (e.g., EURUSD, GBPUSD, XAUUSD).
  - Trading style: Scalper / Day / Swing (controls TF weights).
  - Threshold: Buy Now % ≥ X% or ≤ X% (defaults: ≥70% bullish, ≤30% bearish); direction Buy/Sell/Both.
  - Minimum alignment (optional): require at least N aligned TFs (default off; if on, N=3).
- Trigger
  - Compute Final Score in [−100..+100]; Buy Now % = (FinalScore + 100) / 2.
  - Fire on crossing from the opposite side (e.g., 68% → 72% triggers ≥70%).
  - Apply confirmation on dominant TFs (≥1 closed bar) and respect per‑pair cooldown.
- Suppression/Hysteresis
  - After ≥70% bullish trigger, re‑arm only after Buy Now % falls below 65% and then re‑crosses ≥70%.
  - For ≤30% bearish trigger, re‑arm only after Buy Now % rises above 35% and then re‑crosses ≤30%.
- Example
  - Title: EURUSD · Buy Now 74% (Day)
  - Body: Final Score +48 · New signals: UTBOT 30m, Ichimoku 1h
  - Footer: TF snapshot 15m ✅ · 30m ✅ · 1h ✅ · 4h ⚪ · 1d ⚪ · 12:35 IST

**Type B — Indicator Flip (by TF)**
- Intent: Alert when specific indicator(s) flip on selected timeframe(s).
- Inputs
  - Pairs: 1–3 symbols.
  - Indicators: UTBOT, RSI, MACD, EMA21/50/200, IchimokuClone (choose 1–2).
  - Timeframes: up to 3 (5m, 10m, 15m, 30m, 1h).
  - Direction: Buy / Sell / Both.
  - Only NEW signals: default ON; NEW = flip/cross within last K=3 closed bars.
  - Optional gate: Only alert if Buy Now % (style‑aware) ≥60% for Buy or ≤40% for Sell.
- Trigger (per indicator examples)
  - UTBOT: flip Long/Short.
  - RSI: cross 50 or exit from 30/70 zones matching direction.
  - MACD: MACD/Signal cross with sign agreement (Buy if MACD>Signal and >0; Sell if <Signal and <0).
  - EMA(21/50/200): price crosses EMA in chosen direction and EMA slope confirms (≥0 Buy, ≤0 Sell).
  - IchimokuClone: Tenkan/Kijun cross or cloud breakout per rules.
- Suppression/Hysteresis
  - RSI/EMA: require opposite side touch before re‑alerting same direction (prevents ping‑pong).
  - UTBOT/Ichimoku: fire only on regime flips (no repeats while regime persists).
- Examples
  - Single indicator
    - Title: GBPUSD · UTBOT Buy · 15m
    - Body: Flip to Long. Buy Now 66% (Day). Final Score +32
    - Footer: 10:15 IST · Cooldown 30m
  - Two indicators (RSI + MACD)
    - Title: EURUSD · RSI Buy & MACD Buy · 30m
    - Body: RSI crossed 50↑; MACD>Signal & >0. Buy Now 72% (Day)
    - Footer: 11:45 IST · Cooldown 30m

**Minimal UI**
- Threshold Alerts (Type A): Pair(s), Style, Direction, Threshold slider (20–90, default 70), Optional N‑alignment, Delivery, Cooldown, Save.
- Indicator Alerts (Type B): Pair(s), Indicators, TFs, Direction, NEW toggle, Optional Buy Now % gate, Delivery, Cooldown, Save.
- List view: badge (A or B), config summary, status toggle.

**System Safeguards**
- Rate limit: max 5 alerts/user/hour (overflow → digest).
- Per‑pair concurrency: avoid race conditions.
- Warm‑up: no alerts until lookback satisfied.
- Data gaps: skip TF if candle stale (>2× TF length).

**Message Structure (all channels)**
- Title: {PAIR} · {CONDITION} · {TF/Style}
- Body: reason + Buy Now % + Final Score (if relevant).
- Footer: time (IST) + cooldown note.
- CTA (in‑app): Open chart deep‑link.

**Defaults That Work**
- Type A: 3 pairs, Day style, Buy ≥70%, Sell ≤30%, cooldown 30m.
- Type B: UTBOT, TFs 15m & 30m, NEW ON, cooldown 30m.

**RSI OB/OS — Product & Tech Spec**
1) User Options (UI)
  - Pairs: up to plan limit (3/10/50).
  - Timeframes: up to 3 (5m–1d).
  - Thresholds: Overbought ≥70, Oversold ≤30.
  - RSI length: 14 (allow 7–50).
  - Trigger policy: Crossing (default) or In‑zone.
  - Bar timing: Close (default) or Intrabar.
  - Cooldown: default 30m.
  - Delivery: Email / Telegram.
  - Quiet hours (optional).
  - Free‑text alert name.

2) Data Model
  - alerts: id, user_id, name, symbols, timeframes, rsi_length, overbought, oversold, trigger_policy, bar_policy, cooldown_minutes, channels, quiet_start_local, quiet_end_local, timezone, enabled.
  - alert_state: id, alert_id, symbol, timeframe, last_alert_ts, last_status (neutral|overbought|oversold), last_rsi_value.
  - user_channels: email, telegram_chat_id, bot_token.

3) Evaluation Cadence
  - Run on bar closes (5m, 1h, 1d).
  - Intrabar mode: every N seconds (e.g., 15s) with debounce.

4) RSI Calculation
  - RSI length L (default 14). Wilder’s method. Broker OHLCV feed.

5) Trigger Logic
  - Crossing policy:
    - Overbought: prev_r < OB and r ≥ OB.
    - Oversold: prev_r > OS and r ≤ OS.
  - In‑zone policy: r ≥ OB or r ≤ OS.
  - One alert per side per cooldown.

6) Noise Control
  - Bar‑close only avoids repaint.
  - Intrabar: require 2 consecutive checks.
  - Optional hysteresis: reset at 65/35.

7) Alert Content
  - Email Subject: [RSI Alert] {SYMBOL} {TF} → {STATE} (RSI={VAL})
  - Body: RSI state, thresholds, price, time, policy, cooldown, settings.
  - Telegram: ⚠ RSI {STATE} — {SYMBOL} {TF}, RSI value, threshold, time.

8) Example Config (JSON)
```json
{
  "alert_name": "RSI OB/OS",
  "user_email": "user@example.com",
  "pairs": ["EURUSD"],
  "timeframes": ["30M", "1H"],
  "rsi_period": 14,
  "rsi_overbought_threshold": 70,
  "rsi_oversold_threshold": 30,
  "trigger_policy": "crossing",
  "bar_policy": "close",
  "cooldown_minutes": 30,
  "timezone": "Asia/Kolkata",
  "quiet_start_local": "22:30",
  "quiet_end_local": "06:30",
  "channels": ["email", "telegram"]
}
```

Notes
- This document intentionally focuses on core product behavior and user‑facing configuration. Internal implementation snapshots, migration guides, and parity checklists have been removed to avoid duplication and to keep the spec concise.

