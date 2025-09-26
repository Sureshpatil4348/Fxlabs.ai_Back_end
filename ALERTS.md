**Overview**
- Unified alerts spec for Heat Map Threshold (Type A), Indicator Flip (Type B), RSI OB/OS, and RSI Correlation.
- Delivery channels: Email (implemented) and Telegram (planned). Timestamps display in Asia/Kolkata (IST).
- Trigger philosophy: evaluate on timeframe closes; fire on crossings or regime flips (not every bar while condition remains true); apply cooldowns, hysteresis, Only‑NEW and confirmation to reduce noise.

**Global Rules**
- Max tracked pairs per user: up to 3.
- Delivery channels: Email (Telegram planned; currently disabled).
- Trigger style: crossing into condition or regime flip; 1-bar confirmation on relevant TFs.
- Timezone for display: Asia/Kolkata.
- Rate limits: 5 alerts per user per hour (overflow batched to digest); per‑pair concurrency cap; warm‑up for indicators; skip stale TFs.

**Frontend + Supabase: Consolidated Implementation Guide**
- Scope: This consolidates all Frontend/Supabase follow-ups into concrete, actionable steps for UI, API payloads, and Supabase schema.
- Philosophy: Safe defaults server-side; UI exposes controls progressively; DB constraints guard invalid inputs; no duplication across per-feature notes below.

- Step 1 — Supabase migrations (apply once)
  - Heatmap (`heatmap_alerts`):
    ```sql
    -- Core feature flags and thresholds
    alter table public.heatmap_alerts
      add column if not exists min_alignment integer default 0 check (min_alignment between 0 and 5),
      add column if not exists cooldown_minutes integer default 30 check (cooldown_minutes between 1 and 1440),
      add column if not exists gate_by_buy_now boolean not null default false,
      add column if not exists gate_buy_min numeric(5,2) default 60.00,
      add column if not exists gate_sell_max numeric(5,2) default 40.00,
      add column if not exists trading_style text default 'day' check (trading_style in ('scalper','day','swing')),
      add column if not exists is_active boolean not null default true,
      add column if not exists style_weights_override jsonb;

    -- Helpful indexes
    create index if not exists idx_heatmap_alerts_user_email on public.heatmap_alerts (user_email);
    -- Recommended: index the top-level boolean column (no JSON expression)
    create index concurrently if not exists idx_heatmap_alerts_is_active on public.heatmap_alerts (is_active);
    ```
  - RSI (`rsi_alerts`):
    ```sql
    alter table public.rsi_alerts
      add column if not exists bar_policy text not null default 'close' check (bar_policy in ('close','intrabar')),
      add column if not exists cooldown_minutes integer default 30 check (cooldown_minutes between 1 and 1440),
      add column if not exists trigger_policy text not null default 'crossing' check (trigger_policy in ('crossing','in_zone')),
      add column if not exists only_new_bars smallint not null default 3 check (only_new_bars between 0 and 10),
      add column if not exists confirmation_bars smallint not null default 1 check (confirmation_bars between 0 and 5),
      add column if not exists hysteresis_rearm_ob smallint not null default 65 check (hysteresis_rearm_ob between 0 and 100),
      add column if not exists hysteresis_rearm_os smallint not null default 35 check (hysteresis_rearm_os between 0 and 100),
      add column if not exists timezone text not null default 'Asia/Kolkata',
      add column if not exists quiet_start_local text,
      add column if not exists quiet_end_local text;

    create index if not exists idx_rsi_alerts_user_email on public.rsi_alerts (user_email);
    create index if not exists idx_rsi_alerts_bar_policy on public.rsi_alerts (bar_policy);
    ```
  - RSI Correlation (`rsi_correlation_alerts`): no schema change required for the consolidated features.

- Step 2 — Frontend forms and payloads
  - Heatmap alert creation payload (Type A + optional Type B gating):
    ```json
    {
      "alert_name": "My Heatmap Alert",
      "user_email": "user@example.com",
      "pairs": ["EURUSD","GBPUSD"],
      "timeframes": ["15M","30M","1H"],
      "selected_indicators": ["UTBOT","EMA21","MACD"],
      "trading_style": "day",
      "buy_threshold_min": 70,
      "sell_threshold_max": 30,
      "min_alignment": 0,
      "cooldown_minutes": 30,
      "gate_by_buy_now": false,
      "gate_buy_min": 60,
      "gate_sell_max": 40,
      "notification_methods": ["email"],
      "alert_frequency": "once",
      "trigger_on_crossing": true
    }
    ```
    - UI: add controls for `trading_style` (scalper/day/swing), `min_alignment` (0–5), `cooldown_minutes`, and an advanced section with the gating toggle and numeric inputs.
  - RSI alert creation payload (closed-bar only):
    ```json
    {
      "alert_name": "RSI OB/OS",
      "user_email": "user@example.com",
      "pairs": ["EURUSD"],
      "timeframes": ["30M","1H"],
      "rsi_period": 14,
      "rsi_overbought_threshold": 70,
      "rsi_oversold_threshold": 30,
      "alert_conditions": ["overbought","oversold"],
      "bar_policy": "close",
      "trigger_policy": "crossing",
      "only_new_bars": 3,
      "confirmation_bars": 1,
      "cooldown_minutes": 30,
      "timezone": "Asia/Kolkata",
      "quiet_start_local": "22:30",
      "quiet_end_local": "06:30",
      "notification_methods": ["email"],
      "alert_frequency": "once"
    }
    ```
    Notes:
    - Use keys "overbought"/"oversold" to request threshold crossing detection. When a crossing is confirmed, the backend returns a trigger condition string of "overbought_cross" or "oversold_cross".
    - Closed-only: Backend always evaluates on closed bars, regardless of any stored `bar_policy`. Intrabar (live) evaluation is disabled in this iteration.
    - Current backend API models do not expose `bar_policy`, `only_new_bars`, `confirmation_bars`, or `trigger_policy`.
    - UI: add a “Bar timing” selector (Close/Intrabar), Quiet hours (start/end, local preview), and show cooldown scope note: per (symbol, timeframe, side).

- Step 3 — API and validation tips
  - Send newly added fields as top-level keys in POST bodies; backend uses safe defaults when absent.
  - Validate ranges client-side to mirror DB checks (e.g., `cooldown_minutes` 1–1440; `only_new_bars` 0–10; `confirmation_bars` 0–5).
  - For Max 3 Pairs/User, pre-check UI by aggregating current unique symbols across existing alerts and correlation pairs.

- Step 4 — QA checklist
  - Heatmap: verify triggers at threshold crossings, hysteresis re‑arm (70/65, 30/35), alignment=N behavior, and gating by Buy Now % when toggled.
  - RSI: verify crossing policy w/ 1‑bar confirmation, quiet hours suppression, intrabar mode debounce (if enabled), and per‑side cooldown.
  - Global: confirm per‑pair concurrency (server-side), stale/warm‑up skips, rate limit cap (5/hour) and digest behavior, IST timestamp formatting in emails.

Notes
- No DB change is needed for concurrency, warm‑up/stale‑data handling, global rate limits/digest, or IST formatting — all handled server-side.

**Type A — Buy Now % Threshold (multi‑pair)**
- Intent: Alert when any chosen pair becomes strong enough to act.
- Inputs
  - Pairs: 1–3 symbols.
  - Trading style: Scalper / Day / Swing (controls TF weights).
  - Threshold: Buy Now % ≥ X% or ≤ X% (defaults: ≥70% bullish, ≤30% bearish), direction Buy/Sell/Both.
  - Minimum alignment (optional): require at least N aligned cells across selected TFs (default off; if on default N=3).
- Trigger
  - Compute Final Score in [−100..+100]; Buy Now % = (FinalScore+100)/2 per selected style.
  - Fire on crossing from the opposite side (e.g., 68% → 72% triggers ≥70%). Confirm on dominant TFs for the style (≥1 closed bar).
  - Respect per‑pair cooldown.
- Suppression/Hysteresis
  - After ≥70% bullish trigger, re‑arm only after Buy Now % falls below 65% and then re‑crosses ≥70% (analogous 30%/35% for bearish).
- Example
  - Title: EURUSD · Buy Now 74% (Day)
  - Body: Final Score +48 · New signals: UTBOT 30m, Ichimoku 1h
  - Footer: TF snapshot: 15m ✅ · 30m ✅ · 1h ✅ · 4h ⚪ · 1d ⚪ · at 12:35 IST

**Type B — Indicator Flip (by TF)**
- Intent: Alert when specific indicator(s) flip on selected timeframe(s).
- Inputs
  - Pairs: 1–3 symbols.
  - Indicators: UTBOT, RSI, MACD, EMA21/50/200, IchimokuClone (choose 1–2).
  - Timeframes: up to 3 from 5m, 10m, 15m, 30m, 1h (optionally add 4h/1d later under Higher‑TF).
  - Direction: Buy / Sell / Both.
  - Only NEW signals: default ON; NEW = flip/cross within last K=3 closed bars.
  - Optional gate: Only alert if Buy Now % (style‑aware) ≥60% for Buy or ≤40% for Sell.
- Trigger (per indicator)
  - UTBOT: fire on flip Long/Short (or stop breach) on selected TFs.
  - RSI: cross up 50 (Buy) or cross down 50 (Sell); or exit from 30/70 matching direction.
  - MACD: MACD/Signal cross with sign agreement (Buy if MACD>Signal and >0; Sell if <Signal and <0).
  - EMA(21/50/200): price crosses EMA in chosen direction and EMA slope confirms (≥0 Buy, ≤0 Sell).
  - IchimokuClone: Tenkan/Kijun cross or price cloud breakout per rules.
  - Apply 1‑bar confirmation; apply Only NEW filter; cooldown per (pair, TF, indicator).
- Suppression/Hysteresis
  - RSI/EMA: require opposite side touch before re‑alerting same direction (prevent ping‑pong).
  - UTBOT/Ichimoku: only on regime flips (no repeats while regime persists).
- Examples
  - Title: GBPUSD · UTBOT Buy · 15m — Body: Flip to Long confirmed. Buy Now 66% (Day). Final Score +32. — Footer: 10:15 IST · Cooldown 30m
  - Title: EURUSD · RSI Buy & MACD Buy · 30m — Body: RSI 50↑; MACD>Signal & >0. Buy Now 72% (Day). — Footer: 11:45 IST · Cooldown 30m

**RSI OB/OS Alerts**
- UI
  - Pairs (1–N by plan, e.g., 3/10/50); Timeframes up to 3; Thresholds: Overbought ≥70, Oversold ≤30; RSI length (7–50, default 14).
  - Trigger policy: Crossing (default) or Any in‑zone; Bar timing: On bar close (default) or Intrabar; Cooldown per (pair, TF): default 30 minutes.
  - Delivery: Email/Telegram; Quiet hours (local range, default Asia/Kolkata); Name field.
- Data Model (proposed)
  - Alerts: id, user_id, name, symbols[], timeframes[], rsi_length, overbought, oversold, trigger_policy, bar_policy, cooldown_minutes, deliver_email, deliver_telegram, quiet_start_local, quiet_end_local, timezone, enabled.
  - Alert State: id, alert_id, symbol, timeframe, last_alert_ts (UTC), last_status (neutral|overbought|oversold), last_rsi_value.
  - User Channels: user_id, email, telegram_chat_id (verified), telegram_bot_token (server‑side vault).
- Evaluation Cadence
  - Scheduler aligned with TF closes (bar‑close default): 1m every minute; 5m each 5‑minute boundary; 1h at HH:00; 1d at local midnight or broker close.
  - Intrabar: optional N‑checks debouncing (e.g., 2 checks, 30–60s apart).
- Trigger Logic (per symbol × timeframe)
  - Crossing policy: Overbought CROSS‑IN prev<OB and r≥OB; Oversold CROSS‑IN prev>OS and r≤OS.
  - In‑zone policy: Overbought r≥OB; Oversold r≤OS.
  - One alert per side per cooldown; must be outside quiet hours.
  - State: update last_status, last_rsi_value, last_alert_ts; require bar close if bar_policy=close.
- Debounce/Hysteresis
  - Bar‑close default; Intrabar requires 2 consecutive checks; optional hysteresis (re‑arm at 65/35).
- Templates
  - Email subject: [RSI Alert] {SYMBOL} {TF} → {OVERBOUGHT|OVERSOLD} (RSI={VAL})
  - Telegram: compact message with RSI, threshold, last price, UTC/Local time, policy/bar timing.

**Correlation Alerts (RSI and Actual)**
- Options
  - Modes: RSI‑based correlation mismatch vs Real correlation windows; select up to 3 TFs; choose mismatch type: RSI, Actual, or Both.
- Trigger
  - Fire when mismatch occurs within selected type(s) and TFs; notify immediately, subject to cooldown/frequency settings.

**Minimal UI**
- Threshold Alerts tab (Type A): Pairs, Style, Direction, Threshold slider (20–90, default 70), [Optional] N aligned cells, Delivery channel, Cooldown, Save.
- Indicator Alerts tab (Type B): Pairs, Indicators (1–2), TFs (up to 3), Direction, Only NEW toggle (default ON), [Optional] Gate by Buy Now %, Delivery, Cooldown, Save.
- List view: badge A/B, pairs, threshold/indicators, TFs, direction, cooldown, status toggle.

**System Safeguards**
- Rate limit: 5 alerts/user/hour (overflow to digest).
- Per‑pair concurrency cap: avoid simultaneous evaluations for the same pair.
- Warm‑up: no alerts until each indicator has minimum lookback on that TF.
- Data gaps: if last candle is stale (>2× TF length), skip that TF until data resumes.

**Message Structure**
- Title: {PAIR} · {CONDITION} · {TF/Style}
- Body: short reason + Buy Now % + Final Score (if Type A or gated).
- Footer: time (IST) + cooldown note.
- CTA (in‑app): Open chart deep‑link to heatmap with pair & TF preselected.

**Defaults That Work**
- Type A: 3 pairs, Day style, Buy ≥70%, Sell ≤30%, cooldown 30m.
- Type B: UTBOT default, TFs 15m & 30m, Only NEW ON, cooldown 30m.

**Updates (Scheduling and Retriggering)**
- End‑of‑timeframe evaluation only: if an alert is configured for 5m, evaluate and (if needed) fire at 5‑minute boundaries only; likewise for 15m/30m/1h, etc.
- Retrigger policy: after a fire, do not re‑fire while the metric remains beyond the threshold. Re‑arm only after leaving the zone and then re‑crossing the threshold in the chosen direction. Changing the user’s threshold re‑arms immediately.
- Example: USD/CAD · 30m · crossing ≥80%
  - Checks at 10:00, 10:30, 11:00 …
  - 10:30 close: Buy Now % crosses from 78% → 81% → send alert.
  - Stays >80% at 11:00 → no alert.
  - Falls to 76% at 11:30, then 82% at 12:00 → send alert (re‑cross after re‑arm).

**Current Implementation Snapshot (Backend Repo)**
- Storage and cache
  - Supabase tables: heatmap_alerts, rsi_alerts, rsi_correlation_alerts. An in‑memory cache refreshes periodically; see `app/alert_cache.py`.
- Execution model
  - Alert checkers are launched from the tick loop (see `server.py:876`, `server.py:913`, `server.py:1000+`). Internally:
    - RSI and RSI Correlation enforce bar‑close evaluation using last‑closed‑bar timestamps (default `bar_policy='close'`). RSI values are computed from OHLC close prices.
    - Heat Map checks run on tick invocation; indicator flips use recent bars with confirmation. A dedicated TF scheduler for all alert types is planned.
- Channels
  - Email via SendGrid implemented. Value‑based cooldown: 10 minutes + RSI delta threshold of 5.0; per‑user cap 5/hour with digest.
  - Telegram delivery not implemented (disabled for now).
  - See `app/email_service.py:31` and surrounding lines for cooldown/rate limit/digest.
- Heat Map alerts (Type A + Type B flips)
  - Style‑weighted TF aggregation → Final Score → Buy Now %; optional Minimum Alignment (N TFs). Hysteresis re‑arm (buy_min−5 / sell_max+5). Per (alert, symbol, direction) cooldown (default 30m). Optional gate for Type B flips by Buy Now %.
  - Flips: EMA21/50/200 cross + slope, MACD cross with sign, Ichimoku Tenkan/Kijun cross, simplified UTBOT; Only‑NEW (K=3) and 1‑bar confirmation; see `_detect_indicator_flips` in `app/heatmap_alert_service.py`.
- RSI alerts
  - Crossing policy + 1‑bar confirmation + hysteresis (65/35). Only‑NEW window K=3. Per (alert, symbol, timeframe, side) cooldown (default 30m). Quiet hours with IANA timezone (default Asia/Kolkata). Bar‑close gating via last‑closed‑bar tracking.
  - See `app/rsi_alert_service.py` (e.g., `_get_last_closed_bar_ts`, `_detect_rsi_crossing`).
- RSI correlation alerts
  - Modes: RSI threshold and Real correlation (Pearson of returns, configurable window). Bar‑close gating per timeframe, warm‑up and stale‑bar checks, per‑pair concurrency locks.
  - See `app/rsi_correlation_alert_service.py` (`_check_rsi_threshold_mode`, `_check_real_correlation_mode`, `_calculate_correlation`).
- WebSocket + OHLC
  - OHLC updates are aligned to TF boundaries. A future enhancement will move alert evaluation to these boundaries for full TF parity.
- Templates
  - Rich HTML email templates across alert types; Title/Body/Footer structure documented here.

**Parity Summary (Spec vs Current Code)**

| Area | Item | Parity | Impact |
|------|------|--------|--------|
| Global | Max pairs/user (3) | match | Prevents over‑subscription; enforced at creation across Heatmap/RSI/Correlation (counts unique symbols). |
| Global | Delivery channels (Telegram) | mismatch | Email only; Telegram missing reduces delivery options. |
| Global | Trigger style (RSI crossing + NEW + 1‑bar + hysteresis) | match | Higher signal quality; fallback to in‑zone only when RSI series unavailable. |
| Global | Timezone formatting (IST) | match | Emails display timestamps in Asia/Kolkata (IST) for clarity. |
| Global | Rate limits + digests | match | Per‑user cap: 5 alert emails/hour; overflow is consolidated into a single digest email per hour. |
| Global | Per‑pair concurrency cap | match | Keyed async locks prevent simultaneous evaluations for same pair×TF. |
| Global | Warm‑up / stale‑data skip | match | Skips evaluations when bars are stale (>2× TF) and enforces indicator warm‑up (e.g., RSI lookback). |
| Type A (Heatmap) | Final Score / Buy Now % style weighting | match | Style‑weighted TF aggregation computes Final Score and Buy Now %; thresholds drive BUY/SELL. |
| Type A (Heatmap) | Minimum alignment (N cells) | match | Requires at least N TFs aligned with direction thresholds before triggering. |
| Type A (Heatmap) | Hysteresis (70/65, 30/35) | match | Re‑arm after leaving zone: BUY re‑arms below 65 (5 below buy_min), SELL re‑arms above 35 (5 above sell_max). |
| Type A (Heatmap) | Cooldown policy | match | Per (alert, symbol, direction) cooldown enforced; default 30m, overridable per alert via `cooldown_minutes`. |
| Type B (Flip) | UTBOT/Ichimoku/MACD/EMA flips | match | Flip detection added: EMA cross with slope, MACD cross with sign agreement, Ichimoku Tenkan/Kijun cross, simplified UTBOT (EMA10±0.5×ATR10) with 1‑bar confirmation and Only‑NEW window. |
| Type B (Flip) | Only‑NEW (K=3) and 1‑bar confirmation | match | Implemented in flip detectors (K=3, confirmation=1) across supported indicators. |
| Type B (Flip) | Gate by Buy Now % | match | Optional gate enabled: flips require Buy Now % ≥ buy_min (BUY) or ≤ sell_max (SELL); defaults 60/40. |
| RSI OB/OS | Crossing vs in‑zone | match | Crossing with 1‑bar confirmation and hysteresis implemented; better parity with spec. |
| RSI OB/OS | Evaluation timing | match | Closed-bar only; intrabar (live) evaluation disabled to ensure RSI-closed compliance. |
| RSI OB/OS | Cooldown model | match | Per (alert, symbol, timeframe, side) cooldown enforced; default 30m, overridable via `cooldown_minutes`. |
| RSI OB/OS | Quiet hours / timezone | match | Suppresses alerts within configured local quiet hours using alert timezone (default Asia/Kolkata). |
| Correlation | RSI threshold + real correlation modes | match | Both modes implemented: RSI thresholds and real correlation computed from historical returns over a configurable window. |
| Correlation | TF boundary evaluation + mismatch retriggers | match | Bar‑close evaluation supported; positive/negative mismatch triggers fire only on NEW mismatches and re‑arm after neutral break. |
| Type B (Flip) | Per (pair, TF, indicator) cooldown | mismatch | Not enforced yet; flips rely on Only‑NEW (K=3) + 1‑bar confirmation (and optional Buy Now % gate). |
| RSI OB/OS | RFI conditions (rfi_strong/rfi_moderate) | extra | Optional RFI checks supported alongside RSI; not part of core spec. |
| Global | Alert frequency (once/hourly/daily) | partial | Enforced for Heatmap; not applied to RSI or Correlation services. |
| Global | TF‑boundary evaluation (unified scheduler) | partial | RSI/Correlation use closed‑bar gating internally; Heatmap runs on tick invocation; unified TF scheduler planned. |
| Global | Unsubscribe + email diagnostics | extra | One‑click List‑Unsubscribe, masked config diagnostics, and unsubscribe persistence implemented. |
| Type A (Heatmap) | Style weights override | partial | Field exists in schema; backend calculation does not consume overrides yet. |

**Known Gaps/Notes (Actionable)**
- RSI condition keys: API payload must use "overbought"/"oversold" to enable crossing detection. The backend returns `overbought_cross`/`oversold_cross` when confirmed. The previous example with "overbought_cross" in the input was incorrect and is now fixed.
- In‑zone policy: Pure in‑zone triggers for RSI (without crossing) are not currently wired through the public API path; the service primarily supports crossing with confirmation and hysteresis.
- Evaluation timing: Intrabar (live) evaluation is disabled; backend always performs closed‑bar evaluations.
- Notification methods: Email is the only supported delivery channel for now.
- Type B flips cooldown: Per (pair, TF, indicator) cooldown is not implemented; consider adding an indicator‑scoped cooldown to reduce repeat flip emails.
- Alert frequency: `alert_frequency` is honored in Heatmap but currently ignored by RSI and Correlation services; decide whether to enforce or remove from those models.
- Scheduling parity: All alert types are launched from tick flow; RSI/Correlation enforce closed‑bar gating internally. A shared TF‑boundary scheduler for Heatmap would close the parity gap.
- Style weights override: Schema supports `style_weights_override`; backend ignores it today. Implement to allow per‑alert TF weight customization.
- Minimum alignment default: Backend uses `min_alignment=0` (off) unless explicitly provided. If the UI offers an "enabled" toggle, set N explicitly (recommended N=3) — no implicit default is applied server‑side.

**Frontend/Supabase Follow-ups — Max Pairs/User (3)**
- Frontend
  - Block creation UI when adding new symbols would exceed 3 total unique tracked symbols for the user.
  - Surface backend 400 errors from create endpoints with a friendly message and show remaining slots.
  - Optionally compute available slots by fetching existing alerts and taking the union of symbols; for Correlation alerts, count both symbols in each pair.
- Supabase
  - No direct DB constraint can enforce “max unique symbols per user” across rows. Keep server-side enforcement (now implemented) and optionally add a periodic audit job for compliance.
  - Cache normalization: correlation alerts now expose `correlation_pairs` under the standard `pairs` key in the backend cache for consistency.

**Frontend/Supabase Follow-ups — RSI Crossing/NEW/Confirmation**
- Frontend
  - Update copy to reflect: RSI alerts fire on threshold crossings (OB/OS) with 1‑bar confirmation; default Only‑NEW window = last 3 closed bars.
  - Optionally add toggles for: Trigger policy (Crossing vs In‑Zone), Only‑NEW window (K), and Confirmation bars (default 1). Backend currently uses Crossing+NEW+1‑bar by default.
  - In list/detail views, display the detected trigger type: “overbought cross” or “oversold cross”.
- Supabase
  - Consider extending `rsi_alerts` schema to include: `trigger_policy`, `only_new_bars`, `confirmation_bars`, `hysteresis_rearm_ob`, `hysteresis_rearm_os`.
  - Until schema is extended, server applies defaults (Crossing, K=3, confirm=1, re‑arm 65/35).

**Frontend/Supabase Follow-ups — Per‑Pair Concurrency Cap**
- Frontend
  - None required. Concurrency is enforced server-side and transparent to clients.
- Supabase/Backend
  - No DB change needed. A shared, keyed async lock manager in the backend now caps concurrent evaluations per `symbol:timeframe` across Heatmap, RSI, and RSI Correlation services.

**Frontend/Supabase Follow-ups — Warm‑up/Stale Data**
- Frontend
  - No changes required; behavior is server-side. Optionally surface warnings in UI when alerts are skipped due to warm‑up or stale data to improve user understanding.
- Supabase/Backend
  - None required. Backend skips evaluations if latest bar age exceeds 2× timeframe or if required lookback isn’t met (e.g., RSI needs recent series). Consider telemetry logging table if future reporting is desired.

**Frontend/Supabase Follow-ups — Style‑Weighted Buy Now %**
- Frontend
  - Expose trading style (Scalper/Day/Swing) selection and explain TF emphasis.
  - Display Buy Now % and Final Score in alert lists and emails; show which TFs contributed.
  - Optionally allow custom TF weights per alert (advanced).
- Supabase
  - No immediate schema changes needed (uses existing `trading_style`). Optional future fields: `style_weights_override` for per‑alert customization.

**Frontend/Supabase Follow-ups — Minimum Alignment (N cells)**
- Frontend
  - Add a numeric control (off/0 to 5) for “Minimum aligned TF cells”. Informational helper: counts how many TFs currently align given thresholds.
- Supabase
  - Add `min_alignment` (integer, nullable) to `heatmap_alerts`. Backend respects it when present; default is 0 (disabled).

**Frontend/Supabase Follow-ups — Hysteresis (70/65, 30/35)**
- Frontend
  - Document that after a BUY trigger, another BUY will not fire until Buy Now % drops at least 5 points below buy_min (e.g., 70 → 65) and then crosses again; analogous for SELL (30 → 35).
  - Optionally surface re‑arm thresholds in the alert detail.
- Supabase
  - No schema change required. Hysteresis is managed server‑side in memory per (alert, symbol). Optional: persist state later if durability is needed.

**Frontend/Supabase Follow-ups — Gate by Buy Now % (Type B)**
- Frontend
  - Add a toggle “Gate by Buy Now %” and two numeric inputs: `buy_min` (default 60) and `sell_max` (default 40). Explain that flips are only sent if the style‑weighted Buy Now % passes these gates.
- Supabase
  - Add fields to `heatmap_alerts`: `gate_by_buy_now` (boolean), `gate_buy_min` (numeric), `gate_sell_max` (numeric). Backend reads and applies them when present.

**Frontend/Supabase Follow-ups — RSI Bar Policy**
- Frontend
  - Add a selector for `bar_policy`: Close (default) or Intrabar to the RSI alert form. Tooltip: Close evaluates once per closed bar; Intrabar evaluates on ticks.
- Supabase
  - Add `bar_policy` (text: 'close'|'intrabar') to `rsi_alerts`. Backend defaults to 'close' when absent.

**Frontend/Supabase Follow-ups — RSI Cooldown Model**
- Frontend
  - Add `cooldown_minutes` (default 30) to RSI alert form. Clarify that it applies per symbol and timeframe, separately for overbought vs oversold.
- Supabase
  - Add `cooldown_minutes` (integer, nullable) to `rsi_alerts`. Backend reads it; otherwise uses 30 minutes.

**Frontend/Supabase Follow-ups — RSI Quiet Hours/Timezone**
- Frontend
  - Add fields to RSI alert form: `timezone` (IANA name, default Asia/Kolkata), `quiet_start_local` and `quiet_end_local` (HH:MM). Show a preview of the quiet window in local time.
- Supabase
  - Add `timezone` (text), `quiet_start_local` (text HH:MM), `quiet_end_local` (text HH:MM) to `rsi_alerts`. Backend now reads these fields and suppresses alerts during the configured window.

**Frontend/Supabase Follow-ups — Rate Limits + Digest**
- Frontend
  - Inform users that alert emails are capped at 5/hour per user; overflow is batched into a digest. Consider a UI badge indicating when a digest was sent.
- Supabase/Backend
  - No DB changes needed. Server manages per-user rate limits and digest queues in memory.

**Frontend/Supabase Follow-ups — Timezone (IST) Formatting**
- Frontend
  - None required. Emails display IST automatically. Optionally surface “All times in IST” in UI/notifications for consistency.
- Supabase
  - None.

**Frontend/Supabase Follow-ups — Cooldown Policy**
- Frontend
  - Provide a numeric input for `cooldown_minutes` (default 30) in Heatmap alert form; clarify that it applies per symbol and direction.
- Supabase
  - Add `cooldown_minutes` (integer, nullable) to `heatmap_alerts`. Backend reads and applies if present; otherwise defaults to 30 minutes.
