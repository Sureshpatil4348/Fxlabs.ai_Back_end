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

**Simplified Scope (Current Support)**
- RSI OB/OS Alerts
  - Choose 1–3 timeframes (from supported set).
  - Conditions: Overbought ≥70, Oversold ≤30 (crossing-based triggers).
- RSI Correlation Alerts
  - Modes: RSI Threshold or Real Correlation.
  - Choose 1–3 timeframes.
- Heatmap Alerts (Threshold/Flips)
  - Up to 3 pairs; up to 3 timeframes.
  - Threshold triggers: Buy when Buy Now % ≥ 70 (configurable), Sell when ≤ 30 (configurable). 70–80 typical for Buy.
  - Indicators: choose any 1–2 of the 7 supported indicators (UTBOT, MACD, EMA21/50/200, IchimokuClone, RSI).
- Only the above alert types are in scope for this release.

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
  
  - Free‑text alert name.

2) Data Model
  - alerts: id, user_id, name, symbols, timeframes, rsi_length, overbought, oversold, trigger_policy, bar_policy, cooldown_minutes, channels, enabled.
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
  
  "channels": ["email", "telegram"]
}
```

Notes
- This document intentionally focuses on core product behavior and user‑facing configuration. Internal implementation snapshots, migration guides, and parity checklists have been removed to avoid duplication and to keep the spec concise.


**Supabase — Table Schemas (Canonical)**

- heatmap_alerts
  - id: uuid (pk, default gen_random_uuid())
  - user_id: uuid (nullable, optional linkage)
  - user_email: text (indexed)
  - alert_name: text
  - is_active: boolean (default true)
  - pairs: text[]
  - timeframes: text[]
  - selected_indicators: text[] (values: utbot, macd, ema21, ema50, ema200, ichimokuclone, rsi)
  - trading_style: text (values: scalper|dayTrader|swing)
  - min_alignment: int2 (nullable)
  - buy_threshold_min: int2 (default 70)
  - buy_threshold_max: int2 (default 100)
  - sell_threshold_min: int2 (default 0)
  - sell_threshold_max: int2 (default 30)
  - cooldown_minutes: int2 (default 30)
  - gate_by_buy_now: boolean (default false)
  - gate_buy_min: int2 (default 60)
  - gate_sell_max: int2 (default 40)
  - alert_frequency: text (values: once|hourly|daily, default once)
  - notification_methods: text[] (values subset of ["email","telegram"]) 
  - trigger_on_crossing: boolean (default true)
  - created_at: timestamptz (default now())
  - updated_at: timestamptz (default now())

- rsi_alerts
  - id: uuid (pk)
  - user_id: uuid (nullable)
  - user_email: text (indexed)
  - alert_name: text
  - is_active: boolean (default true)
  - pairs: text[]
  - timeframes: text[]
  - rsi_period: int2 (default 14)
  - rsi_overbought_threshold: int2 (default 70)
  - rsi_oversold_threshold: int2 (default 30)
  - alert_conditions: text[] (values subset of ["overbought","oversold"]) 
  - bar_policy: text (values: close|intrabar, default close)
  - cooldown_minutes: int2 (default 30)
  - timezone: text (default "Asia/Kolkata")
  - quiet_start_local: text (nullable, HH:MM)
  - quiet_end_local: text (nullable, HH:MM)
  - notification_methods: text[] (values subset of ["email","telegram"]) 
  - alert_frequency: text (values: once|hourly|daily, default once)
  - trigger_on_crossing: boolean (default true)
  - created_at: timestamptz (default now())
  - updated_at: timestamptz (default now())

- rsi_alert_triggers (append-only log)
  - id: uuid (pk)
  - alert_id: uuid (fk → rsi_alerts.id)
  - symbol: text
  - timeframe: text
  - trigger_condition: text (values: overbought_cross|oversold_cross)
  - rsi_value: numeric
  - current_price: numeric
  - price_change_percent: numeric
  - triggered_at: timestamptz (default now())

- rsi_correlation_alerts
  - id: uuid (pk)
  - user_id: uuid (nullable)
  - user_email: text (indexed)
  - alert_name: text
  - is_active: boolean (default true)
  - correlation_pairs: jsonb (array of [symbol1, symbol2])
  - timeframes: text[]
  - calculation_mode: text (values: rsi_threshold|real_correlation, default rsi_threshold)
  - rsi_period: int2 (default 14)
  - rsi_overbought_threshold: int2 (default 70)
  - rsi_oversold_threshold: int2 (default 30)
  - correlation_window: int2 (default 50)
  - strong_correlation_threshold: numeric (default 0.70)
  - moderate_correlation_threshold: numeric (default 0.30)
  - weak_correlation_threshold: numeric (default 0.15)
  - notification_methods: text[] (values subset of ["email","telegram"]) 
  - alert_frequency: text (values: once|hourly|daily, default once)
  - trigger_on_crossing: boolean (default true)
  - created_at: timestamptz (default now())
  - updated_at: timestamptz (default now())

- rsi_correlation_alert_triggers (append-only log)
  - id: uuid (pk)
  - alert_id: uuid (fk → rsi_correlation_alerts.id)
  - symbol1: text
  - symbol2: text
  - timeframe: text
  - rsi1: numeric (nullable)
  - rsi2: numeric (nullable)
  - correlation_value: numeric (nullable)
  - current_price1: numeric (nullable)
  - current_price2: numeric (nullable)
  - price_change1: numeric (nullable)
  - price_change2: numeric (nullable)
  - triggered_at: timestamptz (default now())

- user_channels (for Telegram)
  - id: uuid (pk)
  - user_id: uuid (nullable)
  - user_email: text (unique)
  - telegram_chat_id: text (nullable)
  - bot_token: text (nullable)
  - created_at: timestamptz (default now())
  - updated_at: timestamptz (default now())

Indexes & Policies (recommended)
- Create btree indexes on: heatmap_alerts.user_email, rsi_alerts.user_email, rsi_correlation_alerts.user_email.
- Row Level Security (RLS): enable on all tables; policy owner=user_email.
- Triggers: `updated_at` set via trigger on updates.


**Frontend — Exact Implementation Requirements**

Create Alerts UI
- Type A (Heatmap Threshold)
  - Fields: `alert_name`, `pairs` (1–3), `timeframes` (multi), `trading_style` (Scalper/Day/Swing), direction (Buy/Sell/Both), `buy_threshold_min` (slider 20–90, default 70), `sell_threshold_max` (slider 10–50, default 30), optional `min_alignment` (int, default off), `cooldown_minutes` (default 30), delivery `notification_methods` (email, telegram), `alert_frequency` (once/hourly/daily), optional `gate_by_buy_now` with `gate_buy_min`/`gate_sell_max`.
  - Endpoint: POST `/api/heatmap-alerts` with payload reflecting `heatmap_alerts` columns.

- Type B (Indicator Flip)
  - Fields: `alert_name`, `pairs` (1–3), `timeframes` (up to 3), `selected_indicators` (1–2 from UTBOT, RSI, MACD, EMA 21/50/200, IchimokuClone), direction (Buy/Sell/Both), NEW toggle (K=3 fixed), optional `gate_by_buy_now` with 60/40 defaults, `cooldown_minutes`, delivery, `alert_frequency`.
  - Endpoint: same POST `/api/heatmap-alerts` (Type B is part of Heatmap service using `selected_indicators`).
  - Note: Backend currently implements UTBOT/MACD/EMA/Ichimoku flips; RSI flip is pending.

- RSI OB/OS Alerts
  - Fields: `alert_name`, `pairs` (per plan), `timeframes` (up to 3), `rsi_period`, thresholds OB/OS, `alert_conditions` (overbought/oversold), `bar_policy` (close/intrabar; close recommended), `cooldown_minutes`, `timezone`, `quiet_start_local`, `quiet_end_local`, delivery, `alert_frequency`.
  - Endpoint: POST `/api/rsi-alerts`.

- RSI Correlation Alerts
  - Fields: `alert_name`, `correlation_pairs` (list of [symbol1,symbol2]), `timeframes` (up to 3), `calculation_mode` (rsi_threshold/real_correlation), `rsi_period`, OB/OS thresholds, `correlation_window`, strong/moderate/weak thresholds, delivery, `alert_frequency`.
  - Endpoint: POST `/api/rsi-correlation-alerts`.

List & Manage Alerts
- Fetch per user:
  - GET `/api/heatmap-alerts/user/{user_email}`
  - GET `/api/rsi-alerts/user/{user_email}`
  - GET `/api/rsi-correlation-alerts/user/{user_email}`
- Delete heatmap alert: DELETE `/api/heatmap-alerts/{alert_id}`
- Display list view: badge (A/B), summarized config, enabled status.

Validation in UI
- Enforce max 3 unique pairs per user across all alerts (show helpful error if exceeded; backend validates).
- Validate timeframes ≤ 3; enforce supported values (5m,10m,15m,30m,1h,4h,1d).
- Sliders numeric bounds: Buy min 20–90; Sell max 10–50; Cooldown ≥ 5.
- If `gate_by_buy_now` enabled: defaults Buy ≥60 / Sell ≤40 unless overridden.

Delivery Channels
- Email: default enabled.
- Telegram: expose fields to connect chat (store `user_channels.telegram_chat_id` and `bot_token`). Backend sending for Telegram is pending; UI should capture and persist credentials now.

 

Testing Hooks
- Manual check endpoints exist: `/api/*/check`, `/api/*/test-email` — wire QA buttons in dev mode.

**Implementation Parity Notes — Code vs Spec (as of 2025‑09‑26)**
- Global
  - Max 3 unique pairs/user: enforced on create endpoints (Heatmap/RSI/Correlation).
  - Rate limiting (5 alerts/user/hour) and digest batching: implemented in `app/email_service.py` for all alert types; only successful sends count toward the cap; overflows are batched into a digest sent at most once per hour.
  - Per‑pair concurrency and stale‑bar skip: implemented across services.
- RSI OB/OS
  - Triggers: implemented as threshold crossings with Only‑NEW K=3 and 1‑bar confirmation, plus hysteresis re‑arm at 65/35. In‑zone policy is not supported.
  - Evaluation: closed‑bar only; `bar_policy` not exposed via API and not persisted.
  
- RSI Correlation
  - Modes supported: `rsi_threshold` and `real_correlation` (parity OK).
  - Conditions:
    - Real correlation mode uses `strong_positive`, `strong_negative`, `weak_correlation`, `correlation_break` (parity OK).
    - RSI threshold mode uses `positive_mismatch`, `negative_mismatch`, `neutral_break` (not explicitly documented in spec; consider documenting).
  - Frequency: service also recognizes `weekly`, while request model defaults list `once|hourly|daily` (minor mismatch).
- Heatmap (Threshold & Indicator Flips)
  - Thresholding/Hysteresis: implemented; crossing confirmation on “dominant TFs” is not enforced (spec mentions confirmation, code uses hysteresis gating instead).
  - Indicators (strength calc): code additionally accepts `bollinger` and `stochastic` beyond the 7 listed; treat as extras/not in scope UI.
  - Indicator flips implemented for EMA(21/50/200), MACD, Ichimoku (Tenkan/Kijun), UTBOT. RSI flip is not implemented (as noted in spec).
  - Optional `style_weights_override` supported by code but not defined in schema/spec (extra capability).
  - Backend does not enforce “1–2 indicators” nor “≤3 timeframes” — assume UI validation.
  - `trigger_on_crossing` is persisted but not used in flip/threshold evaluation (no‑op currently).
- Delivery
  - Email sending implemented. Telegram capture supported at data level, but sending is not implemented.

### Simplified Scope vs Current Implementation — Summary Table

| Feature | Simplified Scope | Current Implementation | Status | Extras / Pending |
|---|---|---|---|---|
| Max tracked pairs/user | 3 total | Enforced at create for Heatmap/RSI/Correlation | Implemented | — |
| Rate limit + digest | 5 alerts/user/hour + digest | Implemented in email service; successful sends only; overflow batched (≤1/hour) | Implemented | — |
| Per‑pair concurrency | Avoid races | Pair locks across services | Implemented | — |
| Stale TF skip | Skip if last candle age > 2× TF | Implemented in all services | Implemented | — |
| Warm‑up | Indicators require warm‑up | Implemented (e.g., RSI series lookback; Heatmap checks when RSI selected) | Implemented | — |
| Delivery: Email | Supported | Implemented | Implemented | — |
| Delivery: Telegram | Supported | Credentials stored; sending not implemented | Missing | Pending backend sending |
| RSI OB/OS: timeframes | Choose 1–3 | Supported (no strict server validation) | Implemented | UI must enforce ≤3 |
| RSI OB/OS: thresholds/policy | Crossing ≥70/≤30 | Crossing‑only on closed bars; no extra confirmation; threshold‑level re‑arm | Implemented | — |
| RSI OB/OS: in‑zone | Not required | Not supported | N/A | — |
| RSI OB/OS: bar timing | Close | Closed‑bar only (evaluates at TF boundaries) | Implemented | Intrabar disabled |
| RSI OB/OS: cooldown | 30m (configurable) | Configurable via API; persisted to `rsi_alerts.cooldown_minutes` | Implemented | — |
 
| RSI Correlation: modes | RSI Threshold or Real | Both supported | Implemented | — |
| RSI Correlation: conditions (real) | Strong/weak/break rules | Matches spec | Implemented | — |
| RSI Correlation: conditions (RSI) | Threshold concept | Uses `positive_mismatch`/`negative_mismatch`/`neutral_break` | Implemented | Extra conditions vs spec; consider documenting |
| RSI Correlation: TFs | Choose 1–3 | Supported (no strict server validation) | Implemented | UI must enforce ≤3 |
| RSI Correlation: frequencies | once/hourly/daily | Service also supports `weekly` | Implemented | Extra: `weekly` |
| Heatmap: pairs/timeframes | Up to 3 pairs, up to 3 TFs | Supported; global 3‑pair cap enforced; TF count not enforced server‑side | Implemented | UI must enforce ≤3 TFs |
| Heatmap: thresholds | Buy ≥70, Sell ≤30 | Implemented; Buy Now % from style‑weighted Final Score | Implemented | — |
| Heatmap: indicators (selection) | Choose 1–2 of 7 | Supports UTBOT, MACD, EMA21/50/200, IchimokuClone, RSI | Implemented | Backend also accepts `bollinger`, `stochastic` (extras; hide in UI) |
| Heatmap: indicator flips | Not required by simplified scope | Implemented for EMA/ MACD/ Ichimoku/ UTBOT with K=3 + 1‑bar confirm | Extra | Gate by Buy Now % supported |
| Heatmap: RSI flip | If exposed | Not implemented | Missing | Pending if needed |
| Heatmap: style weighting | Implicit | Style defaults implemented; optional `style_weights_override` | Implemented | Extra: override capability |
| Heatmap: trigger_on_crossing | — | Field persisted but not used in evaluation | N/A | No‑op currently |
