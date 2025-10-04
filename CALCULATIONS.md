# Calculations Reference

**IMPORTANT**: As of the latest update, all calculations described in this document are performed **server-side**. The frontend no longer performs any technical indicator calculations client-side. This document serves as a reference for understanding how the calculations work, but the actual computation happens on the backend. The frontend receives pre-calculated indicator values via WebSocket/API.

This document explains, in detail, how every widget computes its values and how the UI derives Buy/Sell labels and percentages. It also calls out data sources, guardrails, and thresholds used across the app.

Contents
- Conventions & Data Inputs
- Static Symbols & Pairs
- Quantum Analysis (Multi‑Indicator Heatmap)
- RSI Overbought/Oversold Tracker
- RSI Correlation Dashboard
- Currency Strength Meter
- RFI (RSI‑Flow Imbalance) Score
- OHLC & Tick Views
- Percentages: Quick Reference
- Implementation File References
 - Timeframe Aliases
 - Settings & Defaults
 - Replication Notes

## Conventions & Data Inputs

- Timeframes: `1M, 5M, 15M, 30M, 1H, 4H, 1D` (and sometimes `1W`).
- Symbols: MT5‑style with trailing `m` in data flow (e.g., `EURUSDm`), displayed to users without `m` as `EUR/USD`.
- Closed‑candle policy: Wherever possible, calculations prefer closed candles to avoid flicker or look‑ahead bias. RSI and correlation explicitly drop the forming candle when sufficient history exists.
- Prices: RSI parity uses Bid closes when available. Other indicators use close unless specified.
- Guardrails: Many calculations clamp outputs and/or return `null` if minimum data is not met. UI shows neutral or placeholders in those cases.

## Static Symbols & Pairs

The app works with the following symbol sets. Internally, streaming uses an `m` suffix (e.g., `EURUSDm`). UI displays without `m` and formats as `EUR/USD`.

FX Core Majors (7):
- EURUSDm, GBPUSDm, USDJPYm, USDCHFm, AUDUSDm, USDCADm, NZDUSDm

FX Crosses (21):
- EURGBPm, EURJPYm, EURCHFm, EURAUDm, EURCADm, EURNZDm
- GBPJPYm, GBPCHFm, GBPAUDm, GBPCADm, GBPNZDm
- AUDJPYm, AUDCHFm, AUDCADm, AUDNZDm
- NZDJPYm, NZDCHFm, NZDCADm
- CADJPYm, CADCHFm
- CHFJPYm

Metals (2):
- XAUUSDm (Gold), XAGUSDm (Silver)

Crypto (2):
- BTCUSDm (Bitcoin), ETHUSDm (Ethereum)

Notes
- RSI Tracker and general dashboards subscribe to all of the above by default.
- Currency Strength only aggregates the eight fiat currencies {USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD}; any pair whose base/quote is outside these (e.g., XAU, XAG, BTC, ETH) is ignored in the strength aggregation.

## Quantum Analysis (Multi‑Indicator Heatmap)

The heatmap aggregates per‑timeframe, per‑indicator signals into a final actionable score, then converts it into Buy% / Sell% and a zone label.

### Inputs & Pre‑processing
- Per timeframe, we fetch OHLC bars and derive the following indicator signals (if minimum bars exist): EMA21/50/200, MACD, RSI, UTBot, Ichimoku Clone.
- “New” signal detection uses a rolling lookback `K = 3` closed candles.
- Quiet‑market safety uses ATR percentiles to dampen MACD/UTBot impact when volatility is abnormally low.

### Indicators

Minimum data requirements (closes/bars):
- EMA21: 21+
- EMA50: 50+
- EMA200: 200+
- MACD(12,26,9): 26+
- RSI(14): 15+
- UTBot: max(EMA50, ATR10) ⇒ 51+
- Ichimoku Clone: 52+ (Senkou B) with 26 shift for Chikou

1) EMA21/EMA50/EMA200
- EMA smoothing: `EMAt = α·Pricet + (1−α)·EMA_{t−1}`, with `α = 2/(period+1)`.
- Slope: `slope = EMAt − EMA_{t−k}` (k=1 by default).
- Signal:
  - buy if `close > EMA` AND `slope ≥ 0`
  - sell if `close < EMA` AND `slope ≤ 0`
  - neutral otherwise
- New: true if a close/EMA cross occurred within last K closed candles.

2) MACD
- MACD line: `EMA_fast(12) − EMA_slow(26)`.
- Signal line: `EMA_9(MACD)`.
- Histogram: `MACD − Signal`.
- Signal:
  - buy if `MACD > Signal` AND `MACD > 0`
  - sell if `MACD < Signal` AND `MACD < 0`
  - neutral otherwise
- New: true if MACD/Signal crossed within last K closed candles.

3) RSI
- Wilder RSI(14):
  - Initial averages (first 14 deltas): `avgGain = sum(gains)/14`, `avgLoss = sum(losses)/14`.
  - Smoothed: `avgGain_t = ((avgGain_{t−1}·(14−1))+gain_t)/14` (same for loss).
  - `RS = avgGain/avgLoss`; `RSI = 100 − 100/(1+RS)`; handle `avgLoss=0 ⇒ RSI=100`.
- Signal:
  - buy if `RSI ≤ 30`
  - sell if `RSI ≥ 70`
  - neutral otherwise
- New: true if RSI crossed 50 or newly entered/exited 30/70 within last K.

4) UTBot (EMA50 + ATR10 with 3× multiplier)
- Baseline: `EMA_50(close)`.
- ATR(10): Wilder true range average.
- Stops: `longStop = baseline − 3·ATR`, `shortStop = baseline + 3·ATR`.
- Position at close: long if `close > shortStop`; short if `close < longStop`; otherwise neutral.
- Signal: buy for long; sell for short; neutral otherwise.
- New: true if a long/short flip occurred within last K.
- Confidence: `min(ATR / MIN_ATR_THRESHOLD, 1.0)` with `MIN_ATR_THRESHOLD=1e−5` (used for display/aux only).

5) Ichimoku Clone
- Tenkan(9): `(max(high[−9..]) + min(low[−9..])) / 2`.
- Kijun(26): `(max(high[−26..]) + min(low[−26..])) / 2`.
- Senkou A: `(Tenkan + Kijun)/2` (projected +26; for signal we compare current price to the resulting cloud levels).
- Senkou B(52): `(max(high[−52..]) + min(low[−52..])) / 2` (projected +26).
- Chikou: `close[−26]`.
- Decision priority (first hit wins):
  1) Price vs cloud: `price > max(SpanA,SpanB) ⇒ buy`, `< min(...) ⇒ sell`, else neutral
  2) Tenkan/Kijun cross: `Tenkan>Kijun ⇒ buy`, `< ⇒ sell`
  3) Cloud color: `SpanA>SpanB ⇒ buy`, `< ⇒ sell`
  4) Chikou vs price[−26]: `>` ⇒ buy, `<` ⇒ sell
- New: true if a Tenkan/Kijun cross or cloud breakout occurred within last K.

6) Quiet‑Market Safety
- Compute ATR series (rolling) and take the last 200 values.
- If current ATR is below the 5th percentile of those values, mark timeframe as quiet.
- Effect: halve per‑cell score contributions for MACD and UTBot on that timeframe.

### Per‑Cell Scoring (per timeframe × indicator)
- Base mapping: buy = `+1`, sell = `−1`, neutral = `0`.
- New‑signal boost: if `new === true`, add `±0.25` in the direction of the signal.
- Quiet‑market damping: if quiet and indicator ∈ {MACD, UTBot}, multiply by `0.5`.
- Clamp: `score ∈ [−1.25, +1.25]`.

### Aggregation to Final Score and Percentages
- Trading‑style timeframe weights (`W_tf(tf)`) examples:
  - scalper: 5M:0.30, 15M:0.30, 30M:0.20, 1H:0.15, 4H:0.05, 1D:0
  - swingTrader: 30M:0.10, 1H:0.25, 4H:0.35, 1D:0.30
- Indicator weights (`W_ind(ind)`) examples:
  - equal: each of {EMA21, EMA50, EMA200, MACD, RSI, UTBOT, IchimokuClone} gets ≈0.1429
  - trendTilted: EMA200:0.15, MACD:0.15, UTBOT:0.15, Ichimoku:0.25, etc.
- Raw aggregate: `Raw = Σ_tf Σ_ind [ S(tf,ind) × W_tf(tf) × W_ind(ind) ]`.
- Final score: `Final = 100 × (Raw / 1.25)` (normalizes cell max to ±100).
- Buy Now %: `Buy% = (Final + 100) / 2`.
- Sell Now %: `Sell% = 100 − Buy%`.

### Actionable Zones (UI label)
- Threshold depends on style:
  - scalper: `|Final| ≥ 25`
  - swingTrader: `|Final| ≥ 15`
- Zone: `buy` if `Final ≥ threshold`; `sell` if `Final ≤ −threshold`; else `wait`.

## RSI Overbought/Oversold Tracker

### RSI Computation
- Wilder RSI(14) using closed candles only (Bid close when available). Forming candle is dropped if enough history exists.

### Zones & Listing
- Oversold list: symbols where `RSI ≤ Oversold` (default 30), sorted ascending by RSI.
- Overbought list: symbols where `RSI ≥ Overbought` (default 70), sorted descending by RSI.

### Daily % Change (shown in table/cards)
- For each symbol `S`:
  - Get current price: latest bid tick if present, else last close.
  - Get “daily open”: prefer daily timeframe (`1D/D1`) open for the current day; fallback to first bar of current day on active timeframe; final fallback to latest bar open.
  - Compute: `Daily% = (current − dailyOpen)/dailyOpen × 100`.

## RSI Correlation Dashboard

Two modes:

### Mode A: RSI Threshold Analysis
- For each configured pair `(A,B)`:
  - Compute `RSI_A`, `RSI_B`.
  - Lookup thresholds: `overbought` (default 70), `oversold` (default 30).
  - Pair type: positive or negative, based on static list.
- Mismatch rules:
  - Positive pairs: mismatch if one is overbought AND the other is oversold.
  - Negative pairs: mismatch if both are simultaneously overbought OR both oversold.
- Display: status (`mismatch`/`neutral`), RSIs for both symbols.

### Mode B: Real Rolling Correlation
- Align closed‑candle closes for both symbols over a window `W` (default 50), using overlapping timestamps across the two series.
- Compute log returns: `r_t = ln(P_t / P_{t−1})` for the aligned sequence.
- Pearson correlation: `corr = cov(r1,r2) / (σ(r1)·σ(r2))`.
- Pair type: positive or negative (from configured list).
- Mismatch thresholds:
  - Positive pair: `corr < +0.25` ⇒ mismatch
  - Negative pair: `corr > −0.15` ⇒ mismatch
- Strength classification: `|corr| ≥ 0.7 ⇒ strong`, `≥ 0.3 ⇒ moderate`, else `weak`.
- Display: correlation as percentage `(corr·100)%`, pair type, mismatch coloring.

### Static Correlation Pairs

The dashboard uses the following fixed correlation list:

Negative pairs
- EURUSD vs USDCHF
- GBPUSD vs USDCHF
- USDJPY vs EURUSD
- USDJPY vs GBPUSD
- USDCAD vs AUDUSD
- USDCHF vs AUDUSD
- XAUUSD vs USDJPY (gold vs USDJPY moderately negative)

Positive pairs
- EURUSD vs GBPUSD
- EURUSD vs AUDUSD
- EURUSD vs NZDUSD
- GBPUSD vs AUDUSD
- AUDUSD vs NZDUSD
- USDCHF vs USDJPY
- XAUUSD vs XAGUSD (gold vs silver very high positive)
- XAUUSD vs EURUSD (safe-haven vs USD)
- BTCUSD vs ETHUSD (very high positive)
- BTCUSD vs XAUUSD (weak/moderate positive)

Window options (rolling): 20, 50, 90, 120 (default 50).

## Currency Strength Meter

Computes an 8‑currency strength map using subscribed FX, metals, and crypto pairs.

### Pairs & Sources
- Pairs: All core majors + cross pairs + `XAUUSDm`, `XAGUSDm`, `BTCUSDm`, `ETHUSDm` are subscribed; calculation uses the eight fiat currencies `{USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD}`.
- Modes:
  - closed: last two closed candles (stable)
  - live: last two ticks (real‑time)

### Enhanced Method (default)
- For each subscribed pair `BASE/QUOTE` with valid previous and current price:
  - Log return: `r = ln(P_t / P_{t−1})`.
  - Contribution:
    - `BASE += r`
    - `QUOTE += −r`
- Average per currency: `SC(currency) = (1/NC) × Σ contributions`.
- Scale around 50: `strength = clamp(20, 80, 50 + average×200)`.
- Normalize currencies that have data to `[10,90]` range, preserving rank.
- Fill any missing currency with neutral `50`. Final safety clamp to `[10,90]`.
- Output: Strength per currency (0–100 like scale used by UI coloring).

### Legacy Method (fallback)
- Simple percentage change per pair: `(P_t − P_{t−1}) / P_{t−1}`.
- Update `BASE += Δ%·2`, `QUOTE −= Δ%·2`; then normalize across currencies to `[0,100]` if variance is meaningful, else fill `50`.

## RFI (RSI‑Flow Imbalance) Score

Combines three “flows” into a composite score in `[-1,1]`, then classifies bullish/bearish/neutral.

- RSI Flow: Sum of last two RSI deltas, normalized to `[-1,1]`.
- Volume Flow: Momentum of last three volumes, normalized to `[-1,1]`.
- Price Flow: Momentum plus mild volatility adjustment, normalized to `[-1,1]`.
- Weighted combination (defaults): `RFI = 0.4·RSI_flow + 0.3·Volume_flow + 0.3·Price_flow`.
- Classification:
  - Bullish if `RFI > 0.6` (strong if `> 0.8`, else moderate)
  - Bearish if `RFI < −0.6` (strong if `< −0.8`, else moderate)
  - Neutral otherwise
- Note: Volume is simulated from price volatility in the current implementation where broker volumes are unavailable.

## OHLC & Tick Views

- OHLC view: Derives candle type (bullish/bearish) and range (High−Low). No trading signal logic.
- Tick view: Displays real‑time tick history and latest quote. No trading signal logic.

## Percentages: Quick Reference

- Heatmap Buy%: `Buy% = (FinalScore + 100)/2`.
- Heatmap Sell%: `Sell% = 100 − Buy%`.
- RSI Tracker Daily%: `((current − dailyOpen) / dailyOpen) × 100`.
- Correlation%: `corr × 100`.
- Currency Strength: rendered as 0–100 style scale after normalization; not a probability.

## Implementation File References

- Heatmap core: `src/components/MultiIndicatorHeatmap.js`
  - Scoring and aggregation: search for `getIndicatorScore`, `calculateFinalScore`.
  - Indicator signals: uses `calculateEMASignals`, `calculateMACDSignals`, `calculateRSISignals`, `generateUTBotSignal`, `calculateIchimokuCloneSignals` from `src/utils/calculations.js`.
- Indicator math: `src/utils/calculations.js`
- RSI Tracker store & daily %: `src/store/useRSITrackerStore.js`
- RSI Correlation: `src/store/useRSICorrelationStore.js`, UI `src/components/RSICorrelationDashboard.js`
- Currency strength store & logic: `src/store/useCurrencyStrengthStore.js`, UI `src/components/CurrencyStrengthMeter.js`
- RFI utilities: `src/utils/rfiCalculations.js`, UI cards `src/components/RFIScoreCard.jsx`
- Formatters (price/percent/labels): `src/utils/formatters.js`

Notes
- All calculations prefer closed candles when feasible to reduce noise.
- Where insufficient data exists, functions return `null` and UI surfaces neutral/placeholder state.
- Thresholds and weights are configurable in code and may be exposed in settings panels.

## Timeframe Aliases

Some data providers use server codes while the UI uses human‑friendly codes. The app normalizes these transparently:
- `1M ↔ M1`
- `5M ↔ M5`
- `15M ↔ M15`
- `30M ↔ M30`
- `1H ↔ H1`
- `4H ↔ H4`
- `1D ↔ D1`
- `1W ↔ W1`

## Settings & Defaults

- RSI period: 14 (user adjustable across dashboards where relevant).
- RSI thresholds: Overbought 70, Oversold 30 (user adjustable in RSI components).
- Heatmap new‑signal lookback: `K = 3` closed candles.
- Quiet‑market safety: ATR lookback 200 values, 5th percentile threshold; halves MACD and UTBot cell scores on quiet timeframes.
- UTBot: EMA length 50, ATR length 10, multiplier 3.0, optional confirmations off, minimum ATR threshold 1e‑5 (for confidence display only).
- Correlation mode: `rsi_threshold` or `real_correlation` (user toggle). Rolling window default 50; options 20/50/90/120.
- Currency Strength: Enhanced method on by default; Legacy method available via settings; mode `closed` prefers closed candles, `live` uses ticks.

## Replication Notes

To reproduce behavior outside this app:
1) Data
   - Subscribe to OHLC by timeframe per symbol, and to ticks if you need `live` modes.
   - Ensure closed‑candle awareness; drop forming bar when enough history exists for RSI and correlation.
2) Indicators
   - Implement Wilder RSI(14), EMA, MACD(12,26,9), ATR(10), UTBot (EMA50±3·ATR), and Ichimoku levels (9/26/52 with 26 shift).
   - Implement “new” signal detection over last K=3 closed candles as described.
3) Scoring & Aggregation (Heatmap)
   - Per‑cell mapping with new‑signal boost and quiet‑market damping; clamp to [−1.25, +1.25].
   - Aggregate via timeframe and indicator weights; normalize by ÷1.25; derive Buy%/Sell%.
4) RSI Tracker
   - Build oversold/overbought lists using thresholds; compute Daily% from day open using 1D if available, else first bar after 00:00 local day boundary on active timeframe; fallback to latest open.
5) Correlation
   - For `real_correlation`, align closes on timestamps; use last `window+1` aligned points; compute log‑return Pearson correlation; classify/mismatch using thresholds provided.
6) Currency Strength
   - Use only fiat currencies in {USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD} for contributions; metals/crypto are ignored by design.
   - Log returns per pair; base gets `+r`, quote gets `−r`; average and normalize to 10–90 with neutral 50 as fallback.
