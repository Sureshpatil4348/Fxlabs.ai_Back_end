import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import logging
import aiohttp

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .email_service import email_service
from .concurrency import pair_locks
from .alert_logging import log_debug, log_info, log_warning, log_error
from .rsi_utils import calculate_rsi_series


configure_logging()
logger = logging.getLogger(__name__)


class HeatmapTrackerAlertService:
    """
    Heatmap/Quantum Analysis Tracker Alert service (single alert per user).
    Uses style-weighted indicator strengths (Buy%/Sell%) and triggers on threshold crossings per pair.
    """

    def __init__(self) -> None:
        # Re-arm per (alert, symbol, side) to avoid re-firing while in-zone
        self._armed: Dict[str, Dict[str, bool]] = {}
        # Supabase creds for trigger logging (tenant-aware)
        from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY
        self.supabase_url = SUPABASE_URL
        self.supabase_service_key = SUPABASE_SERVICE_KEY

    def _key(self, alert_id: str, symbol: str) -> str:
        return f"{alert_id}:{symbol}"

    async def check_heatmap_tracker_alerts(self) -> List[Dict[str, Any]]:
        try:
            all_alerts = await alert_cache.get_all_alerts()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "heatmap_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    user_email = alert.get("user_email", "")
                    style = (alert.get("trading_style") or "scalper").lower()
                    buy_t = float(alert.get("buy_threshold", 70))
                    sell_t = float(alert.get("sell_threshold", 30))
                    pairs: List[str] = alert.get("pairs", []) or []
                    # Start-of-alert evaluation log
                    log_debug(
                        logger,
                        "alert_eval_start",
                        alert_type="heatmap_tracker",
                        alert_id=alert_id,
                        user_email=user_email,
                        style=style,
                        buy_threshold=buy_t,
                        sell_threshold=sell_t,
                        pairs=len(pairs),
                    )
                    # INFO-level concise config
                    log_info(
                        logger,
                        "alert_eval_config",
                        alert_type="heatmap_tracker",
                        alert_id=alert_id,
                        user_email=user_email,
                        style=style,
                        buy_threshold=buy_t,
                        sell_threshold=sell_t,
                        pairs=len(pairs),
                    )

                    ts_iso = datetime.now(timezone.utc).isoformat()
                    per_alert_triggers: List[Dict[str, Any]] = []
                    for symbol in pairs:
                        async with pair_locks.acquire(self._key(alert_id, symbol)):
                            # Compute Buy%/Sell% via real OHLC-derived RSI mapping
                            buy_pct, sell_pct, final_score = await self._compute_buy_sell_percent(symbol, style)
                            rsi_val = buy_pct  # Use Buy% as trigger metric for thresholds
                            log_debug(
                                logger,
                                "heatmap_eval",
                                alert_id=alert_id,
                                symbol=symbol,
                                style=style,
                                buy_percent=round(buy_pct, 2),
                                sell_percent=round(sell_pct, 2),
                                final_score=round(final_score, 2),
                            )
                            k = self._key(alert_id, symbol)
                            st = self._armed.get(k)
                            if st is None:
                                # Startup warm-up: baseline armed-state from current values.
                                # If currently beyond thresholds, mark that side disarmed to avoid immediate trigger.
                                st = {"buy": True, "sell": True}
                                if rsi_val >= buy_t:  # already in BUY zone
                                    st["buy"] = False
                                if rsi_val <= sell_t:  # already in SELL zone (RSI below sell threshold)
                                    st["sell"] = False
                                self._armed[k] = st
                                # Skip triggering on this first observation after baselining
                                continue

                            # Re-arm checks
                            # Buy side re-arms after leaving BUY zone by a margin
                            if not st["buy"] and rsi_val < max(0.0, buy_t - 5):
                                st["buy"] = True
                            # Sell side re-arms after leaving SELL zone by a margin
                            if not st["sell"] and rsi_val > min(100.0, sell_t + 5):
                                st["sell"] = True

                            trig_type: Optional[str] = None
                            # Trigger on RSI threshold crossings with per-side arming
                            if st["buy"] and rsi_val >= buy_t:
                                st["buy"] = False
                                trig_type = "buy"
                            elif st["sell"] and rsi_val <= sell_t:
                                st["sell"] = False
                                trig_type = "sell"

                            if trig_type:
                                per_alert_triggers.append({
                                    "symbol": symbol,
                                    "timeframe": "style-weighted",
                                    "trigger_condition": trig_type,
                                    "buy_percent": round(buy_pct, 2),
                                    "sell_percent": round(sell_pct, 2),
                                    "final_score": round(final_score, 2),
                                    "current_price": None,
                                    "timestamp": ts_iso,
                                })
                                log_info(
                                    logger,
                                    "heatmap_tracker_trigger",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    style=style,
                                    trigger=trig_type,
                                )
                            else:
                                log_debug(
                                    logger,
                                    "heatmap_no_trigger",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    style=style,
                                    buy_percent=round(buy_pct, 2),
                                    sell_percent=round(sell_pct, 2),
                                    buy_threshold=buy_t,
                                    sell_threshold=sell_t,
                                    armed_buy=st.get("buy", True),
                                    armed_sell=st.get("sell", True),
                                )

                    if per_alert_triggers:
                        payload = {
                            "alert_id": alert_id,
                            "alert_name": alert.get("alert_name", "Heatmap Tracker Alert"),
                            "user_email": user_email,
                            "triggered_pairs": per_alert_triggers,
                            "alert_config": alert,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                        }
                        triggers.append(payload)
                        # Fire-and-forget DB log per triggered row
                        for item in per_alert_triggers:
                            asyncio.create_task(self._log_trigger(
                                alert_id=alert_id,
                                symbol=item.get("symbol", ""),
                                trigger_type=item.get("trigger_condition", ""),
                                buy_percent=item.get("buy_percent"),
                                sell_percent=item.get("sell_percent"),
                                final_score=item.get("final_score"),
                            ))
                        # Send email if enabled
                        methods = alert.get("notification_methods") or ["email"]
                        if "email" in methods:
                            log_info(
                                logger,
                                "email_queue",
                                alert_type="heatmap_tracker",
                                alert_id=alert_id,
                            )
                            asyncio.create_task(self._send_email(user_email, payload))
                        else:
                            log_info(
                                logger,
                                "email_disabled",
                                alert_type="heatmap_tracker",
                                alert_id=alert_id,
                                methods=methods,
                            )
                    # End-of-alert evaluation log
                    log_debug(
                        logger,
                        "alert_eval_end",
                        alert_type="heatmap_tracker",
                        alert_id=alert_id,
                        triggered_count=len(per_alert_triggers),
                    )

            return triggers
        except Exception as e:
            logger.error(f"Error checking Heatmap Tracker alerts: {e}")
            return []

    async def _log_trigger(
        self,
        alert_id: str,
        symbol: str,
        trigger_type: str,
        buy_percent: Optional[float],
        sell_percent: Optional[float],
        final_score: Optional[float],
    ) -> None:
        if not self.supabase_url or not self.supabase_service_key:
            return
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.supabase_url}/rest/v1/heatmap_tracker_alert_triggers"
            payload = {
                "alert_id": alert_id,
                "symbol": symbol,
                "trigger_type": trigger_type,
                "buy_percent": buy_percent,
                "sell_percent": sell_percent,
                "final_score": final_score,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status not in (200, 201):
                        txt = await resp.text()
                        log_error(
                            logger,
                            "db_trigger_log_failed",
                            status=resp.status,
                            body=txt,
                            alert_id=alert_id,
                            symbol=symbol,
                            trigger_type=trigger_type,
                        )
                    else:
                        log_info(
                            logger,
                            "db_trigger_logged",
                            alert_id=alert_id,
                            symbol=symbol,
                            trigger_type=trigger_type,
                            buy_percent=buy_percent,
                            sell_percent=sell_percent,
                            final_score=final_score,
                        )
        except Exception as e:
            log_error(
                logger,
                "db_trigger_log_error",
                alert_id=alert_id,
                symbol=symbol,
                trigger_type=trigger_type,
                error=str(e),
            )

    async def _compute_buy_sell_percent(self, symbol: str, style: str) -> (float, float, float):
        """Compute Buy%/Sell% using the Quantum Analysis aggregation per Calculations Reference.

        - Indicators: EMA21/50/200, MACD(12,26,9), RSI(14), UTBot(EMA50 ± 3*ATR10), Ichimoku(9/26/52)
        - New-signal lookback K=3 closed candles
        - Quiet-market damping: halve MACD/UTBot cell scores if ATR is below 5th percentile of last 200 values
        - Scoring per cell: buy=+1, sell=-1, neutral=0; +/−0.25 boost if new; clamp to [-1.25, +1.25]
        - Aggregation: Σ_tf Σ_ind S(tf, ind) × W_tf(tf) × W_ind(ind)
        - Final: 100 × (Raw / 1.25); Buy% = (Final + 100)/2; Sell% = 100 − Buy%
        """
        try:
            from .models import Timeframe as TF
            from .mt5_utils import get_ohlc_data

            style_l = (style or "").lower()
            # Timeframe weights by style
            tf_weights_map = {
                "scalper": {"5M": 0.30, "15M": 0.30, "30M": 0.20, "1H": 0.15, "4H": 0.05, "1D": 0.0},
                "swingtrader": {"30M": 0.10, "1H": 0.25, "4H": 0.35, "1D": 0.30},
            }
            tf_weights = tf_weights_map.get(style_l, tf_weights_map["scalper"])  # default scalper
            active_tfs = [tf for tf, w in tf_weights.items() if w > 0]

            # Indicator weights (equal)
            indicators = ["EMA21", "EMA50", "EMA200", "MACD", "RSI", "UTBOT", "ICHIMOKU"]
            ind_weight = 1.0 / len(indicators)
            ind_weights = {ind: ind_weight for ind in indicators}

            # Helpers
            def ema_series(cl: list, period: int) -> list:
                if len(cl) < period:
                    return []
                k = 2.0 / (period + 1)
                ema_vals = [sum(cl[:period]) / float(period)]
                for price in cl[period:]:
                    ema_vals.append(price * k + ema_vals[-1] * (1 - k))
                return ema_vals

            def macd_series(cl: list, fast: int = 12, slow: int = 26, signal: int = 9):
                if len(cl) < slow + signal:
                    return [], [], []
                ema_fast = ema_series(cl, fast)
                ema_slow = ema_series(cl, slow)
                # Align: ema_fast starts at fast-1, ema_slow at slow-1
                offset = (slow - 1) - (fast - 1)
                macd_line = []
                for i in range(len(ema_slow)):
                    idx_fast = i + offset
                    if idx_fast < 0 or idx_fast >= len(ema_fast):
                        continue
                    macd_line.append(ema_fast[idx_fast] - ema_slow[i])
                sig_line = ema_series(macd_line, signal)
                # Align histogram with sig_line
                hist = []
                if sig_line:
                    start = len(macd_line) - len(sig_line)
                    for i in range(len(sig_line)):
                        hist.append(macd_line[start + i] - sig_line[i])
                return macd_line, sig_line, hist

            def true_range(h: float, l: float, pclose: float) -> float:
                return max(h - l, abs(h - pclose), abs(l - pclose))

            def atr_series(highs: list, lows: list, closes: list, period: int = 10) -> list:
                if len(closes) < period + 1:
                    return []
                trs = []
                for i in range(1, len(closes)):
                    trs.append(true_range(highs[i], lows[i], closes[i - 1]))
                # Wilder smoothing
                atrs = []
                first = sum(trs[:period]) / float(period)
                atrs.append(first)
                for i in range(period, len(trs)):
                    atrs.append(((atrs[-1] * (period - 1)) + trs[i]) / period)
                return atrs

            def percentile(values: list, p: float) -> float:
                if not values:
                    return 0.0
                s = sorted(values)
                k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
                return float(s[k])

            # Compute per-timeframe indicator signals and scores
            K = 3  # lookback for new-signal detection
            tf_map = {"5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}

            raw = 0.0
            for tf_code, w_tf in tf_weights.items():
                if w_tf <= 0:
                    continue
                mtf = tf_map.get(tf_code)
                if not mtf:
                    continue
                bars = get_ohlc_data(symbol, mtf, 260)
                if not bars or len(bars) < 60:
                    continue
                closes = [b.close for b in bars]
                highs = [b.high for b in bars]
                lows = [b.low for b in bars]
                closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
                closed_closes_list = [b.close for b in closed_bars]
                closed_highs = [b.high for b in closed_bars]
                closed_lows = [b.low for b in closed_bars]

                # Precompute series
                ema21 = ema_series(closes, 21)
                ema50 = ema_series(closes, 50)
                ema200 = ema_series(closes, 200)
                macd_line, macd_sig, _ = macd_series(closes)
                rsis = calculate_rsi_series(closed_closes_list, 14)
                atrs = atr_series(closed_highs, closed_lows, closed_closes_list, 10)

                def last_cross(seq_a: list, seq_b: list) -> Optional[int]:
                    # return bars ago where a crossed b (1..K); None if no cross
                    n = min(len(seq_a), len(seq_b))
                    if n < 2:
                        return None
                    a = seq_a[-n:]
                    b = seq_b[-n:]
                    for i in range(2, min(K + 2, n + 1)):
                        p = -i
                        if (a[p] <= b[p] and a[p + 1] > b[p + 1]) or (a[p] >= b[p] and a[p + 1] < b[p + 1]):
                            return i - 1
                    return None

                # Quiet-market detection (ATR 5th percentile over last 200 values)
                is_quiet = False
                if len(atrs) >= 200:
                    last_atr = atrs[-1]
                    p5 = percentile(atrs[-200:], 5.0)
                    is_quiet = last_atr < p5

                def score_cell(signal: str, is_new: bool, ind_name: str) -> float:
                    base = 1.0 if signal == "buy" else (-1.0 if signal == "sell" else 0.0)
                    if base == 0.0:
                        return 0.0
                    if is_new:
                        base = base + (0.25 if base > 0 else -0.25)
                    if is_quiet and ind_name in ("MACD", "UTBOT"):
                        base *= 0.5
                    # Clamp
                    if base > 1.25:
                        base = 1.25
                    if base < -1.25:
                        base = -1.25
                    return base

                # EMA signals
                # Align EMA series with closes
                def ema_signal(ema_vals: list, period: int) -> (str, bool):
                    if len(ema_vals) < 2:
                        return "neutral", False
                    idx_offset = len(closes) - len(ema_vals)
                    close_prev = closes[-2]
                    close_curr = closes[-1]
                    ema_prev = ema_vals[-2]
                    ema_curr = ema_vals[-1]
                    sig = "buy" if (close_curr > ema_curr and (ema_curr - ema_vals[-min(3, len(ema_vals))]) >= 0) else (
                        "sell" if (close_curr < ema_curr and (ema_curr - ema_vals[-min(3, len(ema_vals))]) <= 0) else "neutral"
                    )
                    # New if cross within K
                    crossed = last_cross(closes[idx_offset:], ema_vals)
                    is_new = crossed is not None and crossed <= K
                    return sig, is_new

                # MACD signal
                def macd_signal() -> (str, bool):
                    if len(macd_sig) < 1:
                        return "neutral", False
                    # Align lines
                    m = macd_line[-1]
                    s = macd_sig[-1]
                    sig = "buy" if (m > s and m > 0) else ("sell" if (m < s and m < 0) else "neutral")
                    # New if cross of MACD vs signal in last K
                    crossed = last_cross(macd_line[-len(macd_sig):], macd_sig)
                    is_new = crossed is not None and crossed <= K
                    return sig, is_new

                # RSI signal
                def rsi_signal() -> (str, bool):
                    if len(rsis) < 2:
                        return "neutral", False
                    r = rsis[-1]
                    sig = "buy" if r <= 30 else ("sell" if r >= 70 else "neutral")
                    # New if crossed 50 or entered/exited 30/70 in last K
                    is_new = False
                    window = rsis[-min(len(rsis), K + 1):]
                    for i in range(1, len(window)):
                        prev, curr = window[i - 1], window[i]
                        if (prev < 50 <= curr) or (prev > 50 >= curr):
                            is_new = True
                            break
                        if (prev > 70 and curr <= 70) or (prev < 30 and curr >= 30) or (prev <= 70 and curr > 70) or (prev >= 30 and curr < 30):
                            is_new = True
                            break
                    return sig, is_new

                # UTBot
                def utbot_signal() -> (str, bool):
                    if len(ema50) < 1 or len(atrs) < 1:
                        return "neutral", False
                    # Align baseline with closes
                    baseline = ema50[-1]
                    atr_now = atrs[-1]
                    long_stop = baseline - 3.0 * atr_now
                    short_stop = baseline + 3.0 * atr_now
                    close_now = closes[-1]
                    pos = "buy" if close_now > short_stop else ("sell" if close_now < long_stop else "neutral")
                    # New if flip in last K: check prior pos
                    is_new = False
                    # Build last K positions
                    positions: list = []
                    len_align = min(len(ema50), len(atrs), len(closes))
                    for i in range(1, min(K + 2, len_align + 1)):
                        b = ema50[-i]
                        a = atrs[-i]
                        c = closes[-i]
                        lstop = b - 3.0 * a
                        sstop = b + 3.0 * a
                        p = "buy" if c > sstop else ("sell" if c < lstop else "neutral")
                        positions.append(p)
                    if len(positions) >= 2 and positions[0] != positions[1] and positions[0] in ("buy", "sell"):
                        is_new = True
                    return pos, is_new

                # Ichimoku Clone
                def ichimoku_signal() -> (str, bool):
                    if len(highs) < 52 or len(lows) < 52:
                        return "neutral", False
                    def rolling_mid(vals_high: list, vals_low: list, length: int, idx: int) -> float:
                        h = max(vals_high[idx - length + 1: idx + 1])
                        l = min(vals_low[idx - length + 1: idx + 1])
                        return (h + l) / 2.0
                    idx = len(closes) - 1
                    if idx < 52:
                        return "neutral", False
                    tenkan = rolling_mid(highs, lows, 9, idx)
                    kijun = rolling_mid(highs, lows, 26, idx)
                    span_a = (tenkan + kijun) / 2.0
                    span_b = rolling_mid(highs, lows, 52, idx)
                    # Decision priority
                    up_cloud = max(span_a, span_b)
                    dn_cloud = min(span_a, span_b)
                    price = closes[idx]
                    # 1) Price vs cloud
                    if price > up_cloud:
                        sig = "buy"
                    elif price < dn_cloud:
                        sig = "sell"
                    else:
                        # 2) Tenkan/Kijun cross
                        # Approximate previous values for cross check
                        prev_idx = idx - 1
                        if prev_idx >= 52:
                            tenkan_prev = rolling_mid(highs, lows, 9, prev_idx)
                            kijun_prev = rolling_mid(highs, lows, 26, prev_idx)
                        else:
                            tenkan_prev = tenkan
                            kijun_prev = kijun
                        if tenkan > kijun and tenkan_prev <= kijun_prev:
                            sig = "buy"
                        elif tenkan < kijun and tenkan_prev >= kijun_prev:
                            sig = "sell"
                        else:
                            # 3) Cloud color
                            if span_a > span_b:
                                sig = "buy"
                            elif span_a < span_b:
                                sig = "sell"
                            else:
                                # 4) Chikou vs price[-26]
                                if idx >= 26:
                                    chikou = closes[idx - 26]
                                    price_prev = closes[idx - 26]
                                    if chikou > price_prev:
                                        sig = "buy"
                                    elif chikou < price_prev:
                                        sig = "sell"
                                    else:
                                        sig = "neutral"
                                else:
                                    sig = "neutral"
                    # New if TK cross or cloud breakout in last K
                    is_new = False
                    # Check TK cross in last K
                    crosses = 0
                    for i in range(1, K + 1):
                        j = idx - i
                        if j < 52:
                            break
                        tk = rolling_mid(highs, lows, 9, j)
                        kj = rolling_mid(highs, lows, 26, j)
                        tk_prev = rolling_mid(highs, lows, 9, j - 1) if j - 1 >= 26 else tk
                        kj_prev = rolling_mid(highs, lows, 26, j - 1) if j - 1 >= 26 else kj
                        if (tk_prev <= kj_prev and tk > kj) or (tk_prev >= kj_prev and tk < kj):
                            crosses += 1
                            break
                    if crosses > 0:
                        is_new = True
                    else:
                        # Cloud breakout
                        for i in range(1, K + 1):
                            j = idx - i
                            if j < 52:
                                break
                            ten = rolling_mid(highs, lows, 9, j)
                            kij = rolling_mid(highs, lows, 26, j)
                            sa = (ten + kij) / 2.0
                            sb = rolling_mid(highs, lows, 52, j)
                            up_c = max(sa, sb)
                            dn_c = min(sa, sb)
                            pr = closes[j]
                            if pr > up_c or pr < dn_c:
                                is_new = True
                                break
                    return sig, is_new

                # Evaluate each indicator cell
                per_tf_sum = 0.0
                # EMA21
                sig, is_new = ema_signal(ema21, 21)
                per_tf_sum += score_cell(sig, is_new, "EMA21") * ind_weights["EMA21"]
                # EMA50
                sig, is_new = ema_signal(ema50, 50)
                per_tf_sum += score_cell(sig, is_new, "EMA50") * ind_weights["EMA50"]
                # EMA200
                sig, is_new = ema_signal(ema200, 200)
                per_tf_sum += score_cell(sig, is_new, "EMA200") * ind_weights["EMA200"]
                # MACD
                sig, is_new = macd_signal()
                per_tf_sum += score_cell(sig, is_new, "MACD") * ind_weights["MACD"]
                # RSI
                sig, is_new = rsi_signal()
                per_tf_sum += score_cell(sig, is_new, "RSI") * ind_weights["RSI"]
                # UTBot
                sig, is_new = utbot_signal()
                per_tf_sum += score_cell(sig, is_new, "UTBOT") * ind_weights["UTBOT"]
                # Ichimoku Clone
                sig, is_new = ichimoku_signal()
                per_tf_sum += score_cell(sig, is_new, "ICHIMOKU") * ind_weights["ICHIMOKU"]

                raw += per_tf_sum * w_tf

            # Normalize and derive percentages
            final = 100.0 * (raw / 1.25)
            if final > 100.0:
                final = 100.0
            if final < -100.0:
                final = -100.0
            buy_pct = (final + 100.0) / 2.0
            sell_pct = 100.0 - buy_pct
            return float(buy_pct), float(sell_pct), float(final)
        except Exception:
            return 50.0, 50.0, 0.0

    async def _send_email(self, user_email: str, payload: Dict[str, Any]) -> None:
        try:
            await email_service.send_heatmap_tracker_alert(
                user_email=user_email,
                alert_name=payload.get("alert_name", "Heatmap Tracker Alert"),
                triggered_pairs=payload.get("triggered_pairs", []),
                alert_config=payload.get("alert_config", {}),
            )
        except Exception as e:
            logger.error(f"Error sending Heatmap Tracker email: {e}")


heatmap_tracker_alert_service = HeatmapTrackerAlertService()
