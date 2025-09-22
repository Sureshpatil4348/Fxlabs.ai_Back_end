**Overview**
- Unified alerts specification for Heat Map Threshold (Type A), Indicator Flip (Type B), RSI OB/OS, and RSI Correlation.
- Delivery channels target Email and Telegram; timestamps use Asia/Kolkata (IST).
- Trigger philosophy: fire on crossings or regime flips, not on every bar while the condition remains true; add cooldowns and hysteresis to reduce noise.

**Global Rules**
- Max tracked pairs per user: up to 3.
- Delivery channels: Email and Telegram (both may be selected).
- Trigger style: crossing into condition or regime flip; 1-bar confirmation on relevant TFs.
- Timezone for display: Asia/Kolkata.
- Rate limits: 5 alerts per user per hour (overflow batched to digest); per‑pair concurrency cap; warm‑up for indicators; skip stale TFs.

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
  - Scheduler aligned with TF closes: 1m every minute; 5m each 5‑minute boundary; 1h at HH:00; 1d at local midnight or broker close.
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
- End‑of‑TF evaluation only: if alert is configured for 5m, evaluate and fire only at 5‑minute boundaries; likewise for 30m/1h.
- Retrigger only on re‑cross: once fired at a threshold, do not re‑fire while condition remains beyond threshold; re‑fire only after exiting and re‑crossing the threshold (or if the user changes the threshold).
- Example: USD/CAD · 30m · crossing ≥80%
  - System checks at 10:00, 10:30, 11:00…
  - If Buy Now % crosses 80% and becomes 81% at a bar close → send alert.
  - Continues checking each 30m; if stays >80% → no alert.
  - If dips back below 80% and later re‑crosses ≥80% → send alert.

**Current Implementation Snapshot (Backend Repo)**
- Storage and cache
  - Supabase tables: heatmap_alerts, rsi_alerts, rsi_correlation_alerts (loaded into an in‑memory cache every 5 minutes); see `app/alert_cache.py:1`.
- Execution model
  - Alerts evaluated on ticks in background, not aligned to bar closes; see `server.py:781` invoking all checkers each tick batch.
- Channels
  - Email via SendGrid implemented; Telegram not implemented. Value‑based cooldown in email sender: 10 minutes by default and RSI delta threshold of 5.0; see `app/email_service.py:24` and `app/email_service.py:31`.
- Heat Map alerts
  - Service computes a simple indicator mix with RSI and simulated others; triggers when RSI value falls in configured buy/sell ranges; frequency gating by “once/hourly/daily”; see `app/heatmap_alert_service.py:24` and `_should_trigger_alert` at `app/heatmap_alert_service.py:153`.
- RSI alerts
  - Triggers when RSI is in‑zone for configured conditions (overbought/oversold), plus optional simulated RFI checks; 5‑minute per‑alert cooldown; see `app/rsi_alert_service.py:25` and `_check_rsi_conditions` at `app/rsi_alert_service.py:377`.
- RSI correlation alerts
  - Supports both RSI threshold mode and real correlation mode; gating by alert_frequency (once/hourly/daily/weekly); see `_should_trigger_alert` at `app/rsi_correlation_alert_service.py:27`.
- WebSocket scheduling
  - OHLC broadcasts are aligned to TF boundaries, but alert evaluation is currently tick‑driven (not bar‑close); see `server.py:940` onward for OHLC scheduling.
- Templates
  - HTML email bodies for Heatmap, RSI, and RSI Correlation are implemented with branding; titles and content are descriptive but not in the compact “Title/Body/Footer” text form.

**Parity Summary (Spec vs Current Code)**
- Global
  - Max pairs/user (3): enforced in backend at creation time (counts unique symbols across Heatmap, RSI, and both sides of Correlation pairs).
  - Delivery channels: Email implemented; Telegram missing.
  - Trigger style: crossing/new‑only for RSI is now enforced with 1‑bar confirmation and hysteresis re‑arm (70/65 and 30/35). In‑zone fallback still used only when historical RSI series is unavailable.
  - Timezone: emails use UTC; IST formatting not applied.
  - Rate limits/digest: not implemented (only test‑email rate limits exist in `server.py`).
  - Per‑pair concurrency cap: not implemented.
  - Warm‑ups/data‑gap handling: not explicitly implemented.
- Type A — Buy Now %
  - Final Score/Buy Now % computation by style: not present (current heatmap uses indicator scores with RSI emphasis; no TF weighting by style).
  - Minimum alignment N cells: not implemented.
  - Hysteresis 70/65 and 30/35: not implemented.
  - Cooldown: frequency‑based (once/hourly/daily) rather than per‑pair crossing logic.
- Type B — Indicator Flip
  - UTBOT/Ichimoku/MACD/EMA flip logic: not implemented; indicator values are simulated for heatmap strength, not regime flips.
  - “Only NEW” (K=3 bars) and 1‑bar confirmation: not implemented.
  - Optional gate by Buy Now %: not implemented.
- RSI OB/OS
  - Crossing vs in‑zone policies: only in‑zone implemented; no prev‑bar cross detection.
  - Bar close vs intrabar: alerts are tick‑driven; no bar policy.
  - Cooldown: implemented per alert (5 minutes default), but not per (pair, timeframe) and not tied to re‑cross hysteresis.
  - Quiet hours/timezone: not implemented.
- Correlation Alerts
  - RSI threshold and real correlation modes: present; evaluation is tick‑driven and gated by alert_frequency.
  - Timeframe boundary evaluation and mismatch retrigger rules: not implemented.

**Implementation Plan to Reach Parity**
- Scheduling and state
  - Add a bar‑aligned scheduler per TF. At each TF close, enqueue evaluations for all active alerts covering that TF. Derive next bars using existing OHLC scheduling logic in `server.py`.
  - Introduce per‑(alert, symbol, timeframe, indicator) state in DB: last_status, last_value, last_alert_ts; use it for crossing and NEW detection.
  - Enforce per‑pair concurrency via a keyed async lock.
- Trigger logic
  - Implement crossing detection using last closed bar values. For RSI, store prev_r and r; for EMA/RSI/50‑line, detect cross‑ins; for UTBOT/Ichimoku/MACD, implement regime flips with 1‑bar confirmation.
  - Add hysteresis thresholds (e.g., 70/65, 30/35) and “Only NEW” window K=3.
  - Implement Buy Now % pipeline: compute Final Score with style‑weighted TFs and optional minimum alignment N.
- Cooldowns and rate limits
  - Replace time‑only cooldowns with per‑(pair, TF, indicator) re‑arm based on exit and re‑cross; keep a short safety cooldown.
  - Add per‑user hourly cap (5) and digest for overflow.
- Delivery and content
  - Add Telegram bot integration and per‑user verification flow; batch API calls with backoff.
  - Switch message formatting to consistent Title/Body/Footer with IST timestamps; add deep‑link CTA.
- Safeguards
  - Warm‑up: ensure lookbacks are satisfied per indicator/TF; skip if stale bar age > 2× TF.
  - Quiet hours: suppress within configured local window using IANA timezone.
- Data model
  - Extend Supabase schemas to include: bar_policy, trigger_policy, only_new, min_alignment, style, cooldown_minutes, deliver_telegram, timezone, quiet hours; add alert_state tables for RSI/TypeB and TypeA.

**Open Questions**
- Minimum viable set of indicators for Type B in v1 (UTBOT+RSI+EMA?)
- Specific TF weighting for styles (Scalper/Day/Swing) and source of Final Score.
- Do we gate Type B by Buy Now % by default, and at what levels?

**What Is Implemented Today (Quick References)**
- Email cooldown and value similarity: `app/email_service.py:31` (10m) and RSI delta 5.0 at `app/email_service.py:32`.
- Tick‑driven alert checks: `server.py:781`.
- Heatmap alerts service and frequency gating: `app/heatmap_alert_service.py:24`, `_should_trigger_alert` `app/heatmap_alert_service.py:153`.
- RSI alerts with in‑zone checks and 5‑minute cooldown: `app/rsi_alert_service.py:25`, `_check_rsi_conditions` `app/rsi_alert_service.py:377`.
- RSI correlation alerts (threshold + real correlation): `_should_trigger_alert` `app/rsi_correlation_alert_service.py:27`.

**Parity Statement**
- Core coverage exists for Email delivery, basic Heatmap alerts, RSI alerts, and RSI Correlation alerts, but the current system is tick‑driven with in‑zone triggers and time/value cooldowns. The product spec requires bar‑close scheduling, crossing/flip detection with hysteresis, NEW‑only and confirmation logic, Buy Now % with style weights and minimum alignment, and Telegram delivery. These items are not yet implemented and represent the primary parity gaps.

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
