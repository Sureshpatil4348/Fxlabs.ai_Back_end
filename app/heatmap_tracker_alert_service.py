import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import logging

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .email_service import email_service
from .concurrency import pair_locks
from .alert_logging import log_debug, log_info, log_warning, log_error
from .indicator_cache import indicator_cache
from .indicators import (
    rsi_series as ind_rsi_series,
    ema_series as ind_ema_series,
    macd_series as ind_macd_series,
    atr_wilder_series as ind_atr_wilder_series,
    utbot_series as ind_utbot_series,
    ichimoku_series as ind_ichimoku_series,
)
from .quantum import compute_quantum_for_symbol


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
            # Event-driven paths should not force cache refresh; use snapshot to avoid blocking
            all_alerts = await alert_cache.get_all_alerts_snapshot()
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
                            # Pair evaluation start (verbose)
                            k = self._key(alert_id, symbol)
                            prev_state = self._armed.get(k)
                            log_debug(
                                logger,
                                "pair_eval_start",
                                alert_id=alert_id,
                                symbol=symbol,
                                style=style,
                                buy_threshold=buy_t,
                                sell_threshold=sell_t,
                                prev_armed_buy=(prev_state or {}).get("buy") if prev_state else None,
                                prev_armed_sell=(prev_state or {}).get("sell") if prev_state else None,
                            )
                            log_debug(
                                logger,
                                "pair_eval_metrics",
                                alert_id=alert_id,
                                symbol=symbol,
                                buy_percent=round(buy_pct, 2),
                                sell_percent=round(sell_pct, 2),
                                final_score=round(final_score, 2),
                            )
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
                                log_debug(
                                    logger,
                                    "pair_eval_decision",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    decision="baseline_skip",
                                    armed_buy=st.get("buy"),
                                    armed_sell=st.get("sell"),
                                )
                                # Skip triggering on this first observation after baselining
                                continue

                            # Re-arm checks (no margin): re-arm as soon as we leave the zone boundary
                            # Buy side re-arms after leaving BUY zone
                            if not st["buy"] and rsi_val < buy_t:
                                st["buy"] = True
                                log_debug(
                                    logger,
                                    "pair_rearm",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    side="buy",
                                    rearm_threshold=buy_t,
                                    buy_percent=round(rsi_val, 2),
                                )
                            # Sell side re-arms after leaving SELL zone
                            if not st["sell"] and rsi_val > sell_t:
                                st["sell"] = True
                                log_debug(
                                    logger,
                                    "pair_rearm",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    side="sell",
                                    rearm_threshold=sell_t,
                                    buy_percent=round(rsi_val, 2),
                                )

                            # Criteria snapshot (verbose): show exactly what we compare against
                            buy_rearm_th = buy_t
                            sell_rearm_th = sell_t
                            equiv_sell_pct_th = 100.0 - sell_t
                            log_debug(
                                logger,
                                "pair_eval_criteria",
                                alert_id=alert_id,
                                symbol=symbol,
                                style=style,
                                buy_percent=round(rsi_val, 2),
                                buy_threshold=buy_t,
                                sell_percent=round(sell_pct, 2),
                                sell_threshold=sell_t,
                                sell_equiv_percent_threshold=round(equiv_sell_pct_th, 2),
                                armed_buy=st.get("buy", True),
                                armed_sell=st.get("sell", True),
                                rearm_buy_threshold=round(buy_rearm_th, 2),
                                rearm_sell_threshold=round(sell_rearm_th, 2),
                                can_trigger_buy=bool(st.get("buy", True) and rsi_val >= buy_t),
                                can_trigger_sell=bool(st.get("sell", True) and rsi_val <= sell_t),
                            )

                            trig_type: Optional[str] = None
                            # Trigger on RSI threshold crossings with per-side arming
                            if st["buy"] and rsi_val >= buy_t:
                                st["buy"] = False
                                trig_type = "buy"
                            elif st["sell"] and rsi_val <= sell_t:
                                st["sell"] = False
                                trig_type = "sell"

                            if trig_type:
                                log_debug(
                                    logger,
                                    "pair_eval_decision",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    decision="trigger",
                                    trigger=trig_type,
                                    buy_percent=round(rsi_val, 2),
                                    threshold=(buy_t if trig_type == "buy" else sell_t),
                                )
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
                                # Explain why no trigger occurred
                                reason = "within_neutral_band"
                                if rsi_val < buy_t and rsi_val > sell_t:
                                    reason = "within_neutral_band"
                                elif st.get("buy") and rsi_val < buy_t:
                                    reason = "below_buy_threshold"
                                elif st.get("sell") and rsi_val > sell_t:
                                    reason = "above_sell_threshold"
                                elif not st.get("buy") and rsi_val >= buy_t:
                                    reason = "buy_disarmed"
                                elif not st.get("sell") and rsi_val <= sell_t:
                                    reason = "sell_disarmed"
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
                                    reason=reason,
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
                        # DB trigger logging removed per product decision
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

    # DB trigger logging removed

    async def _compute_buy_sell_percent(self, symbol: str, style: str) -> (float, float, float):
        """Compute Buy%/Sell% using cache-derived indicators and centralized helpers.

        - Uses cached RSI(14), EMA(21/50/200), MACD(12,26,9) from `indicator_cache`.
        - Computes UTBot and Ichimoku via `app.indicators` on closed OHLC (no inline math).
        - New-signal lookback K=3 closed candles.
        - Quiet-market damping: halve MACD/UTBot cell scores if ATR10 is below 5th percentile of the last 200 values.
        - Aggregation per spec; equal indicator weights within each timeframe; style-weighted across TFs.
        """
        try:
            from .models import Timeframe as TF
            from .mt5_utils import get_ohlc_data

            style_l = (style or "").lower()
            tf_weights_map = {
                "scalper": {"5M": 0.30, "15M": 0.30, "30M": 0.20, "1H": 0.15, "4H": 0.05, "1D": 0.0},
                "swingtrader": {"30M": 0.10, "1H": 0.25, "4H": 0.35, "1D": 0.30},
            }
            tf_weights = tf_weights_map.get(style_l, tf_weights_map["scalper"])  # default scalper

            indicators = ["EMA21", "EMA50", "EMA200", "MACD", "RSI", "UTBOT", "ICHIMOKU"]
            ind_weight = 1.0 / len(indicators)
            ind_weights = {ind: ind_weight for ind in indicators}

            K = 3
            tf_map = {"5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}

            # Fast-path: reuse centralized quantum computation to ensure single source of truth.
            try:
                q = await compute_quantum_for_symbol(symbol)
                overall = q.get("overall", {}) if isinstance(q, dict) else {}
                style_key = style_l if style_l in ("scalper", "swingtrader") else "scalper"
                v = overall.get(style_key)
                if v and all(k in v for k in ("buy_percent", "sell_percent", "final_score")):
                    return float(v["buy_percent"]), float(v["sell_percent"]), float(v["final_score"])
            except Exception:
                # Fall back to local computation below on any error
                pass

            def percentile(values: list, p: float) -> float:
                if not values:
                    return 0.0
                s = sorted(values)
                k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
                return float(s[k])

            def clamp(x: float, lo: float, hi: float) -> float:
                return hi if x > hi else (lo if x < lo else x)

            raw = 0.0
            for tf_code, w_tf in tf_weights.items():
                if w_tf <= 0:
                    continue
                mtf = tf_map.get(tf_code)
                if not mtf:
                    continue

                # Fetch closed OHLC for alignment and UTBot/Ichimoku/ATR
                bars = get_ohlc_data(symbol, mtf, 300)
                if not bars:
                    continue
                closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
                if len(closed_bars) < 60:
                    continue
                closes = [b.close for b in closed_bars]
                highs = [b.high for b in closed_bars]
                lows = [b.low for b in closed_bars]
                ts_list = [int(b.time) for b in closed_bars]
                ts_to_close: Dict[int, float] = {int(b.time): float(b.close) for b in closed_bars}

                # Quiet market detection
                atrs = ind_atr_wilder_series(highs, lows, closes, 10)
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
                    return clamp(base, -1.25, 1.25)

                # -----------------
                # RSI(14) from cache (fallback compute if needed)
                # -----------------
                rsi_recent = await indicator_cache.get_recent_rsi(symbol, tf_code, 14, K + 2)
                if not rsi_recent or len(rsi_recent) < 2:
                    try:
                        rsis = ind_rsi_series(closes, 14)
                        if rsis:
                            rsi_recent = [(ts_list[-len(rsis) + i], rsis[i]) for i in range(len(rsis))][- (K + 2):]
                    except Exception:
                        rsi_recent = None

                def rsi_signal_from_recent() -> (str, bool):
                    if not rsi_recent or len(rsi_recent) < 2:
                        return "neutral", False
                    r = float(rsi_recent[-1][1])
                    sig = "buy" if r <= 30 else ("sell" if r >= 70 else "neutral")
                    is_new = False
                    window = [float(v) for _, v in rsi_recent[- (K + 1):]]
                    for i in range(1, len(window)):
                        prev, curr = window[i - 1], window[i]
                        if (prev < 50 <= curr) or (prev > 50 >= curr):
                            is_new = True
                            break
                        if (prev > 70 and curr <= 70) or (prev < 30 and curr >= 30) or (prev <= 70 and curr > 70) or (prev >= 30 and curr < 30):
                            is_new = True
                            break
                    return sig, is_new

                # -----------------
                # EMA from cache (align with closes by timestamp)
                # -----------------
                ema_recent_21 = await indicator_cache.get_recent_ema(symbol, tf_code, 21, K + 3)
                ema_recent_50 = await indicator_cache.get_recent_ema(symbol, tf_code, 50, K + 3)
                ema_recent_200 = await indicator_cache.get_recent_ema(symbol, tf_code, 200, K + 3)

                def ema_signal_from_recent(ema_recent: Optional[List[Tuple[int, float]]], label: str) -> (str, bool):
                    if not ema_recent or len(ema_recent) < 2:
                        return "neutral", False
                    # Align on timestamps
                    aligned: List[Tuple[int, float, float]] = []  # (ts, close, ema)
                    for ts, ev in ema_recent:
                        c = ts_to_close.get(int(ts))
                        if c is not None:
                            aligned.append((int(ts), float(c), float(ev)))
                    if len(aligned) < 2:
                        return "neutral", False
                    _, c_prev, e_prev = aligned[-2]
                    _, c_curr, e_curr = aligned[-1]
                    sig = "buy" if c_curr > e_curr else ("sell" if c_curr < e_curr else "neutral")
                    # Cross within last K
                    is_new = False
                    for i in range(1, min(K, len(aligned) - 1) + 1):
                        _, cp, ep = aligned[-(i + 1)]
                        _, cc, ec = aligned[-i]
                        if (cp <= ep and cc > ec) or (cp >= ep and cc < ec):
                            is_new = True
                            break
                    return sig, is_new

                # -----------------
                # MACD from cache
                # -----------------
                macd_recent = await indicator_cache.get_recent_macd(symbol, tf_code, 12, 26, 9, K + 3)

                def macd_signal_from_recent() -> (str, bool):
                    if not macd_recent or len(macd_recent) < 1:
                        return "neutral", False
                    _, m, s, _h = macd_recent[-1]
                    sig = "buy" if (m > s and m > 0) else ("sell" if (m < s and m < 0) else "neutral")
                    # Cross within last K
                    is_new = False
                    for i in range(1, min(K, len(macd_recent) - 1) + 1):
                        _, m_prev, s_prev, _ = macd_recent[-(i + 1)]
                        _, m_curr, s_curr, _ = macd_recent[-i]
                        if (m_prev <= s_prev and m_curr > s_curr) or (m_prev >= s_prev and m_curr < s_curr):
                            is_new = True
                            break
                    return sig, is_new

                # -----------------
                # UTBot via centralized helper
                # -----------------
                def utbot_signal() -> (str, bool):
                    res = ind_utbot_series(highs, lows, closes, 50, 10, 3.0)
                    base = res.get("baseline") or []
                    l = res.get("long_stop") or []
                    s = res.get("short_stop") or []
                    flips = res.get("buy_sell_signal") or []
                    if not (base and l and s):
                        return "neutral", False
                    price = closes[-1]
                    pos = "buy" if price > s[-1] else ("sell" if price < l[-1] else "neutral")
                    is_new = any(v != 0 for v in flips[-K:]) if flips else False
                    return pos, is_new

                # -----------------
                # Ichimoku via centralized helper
                # -----------------
                def ichimoku_signal() -> (str, bool):
                    series = ind_ichimoku_series(highs, lows, closes, 9, 26, 52, 26)
                    tenkan = series.get("tenkan") or []
                    kijun = series.get("kijun") or []
                    sa = series.get("senkou_a") or []
                    sb = series.get("senkou_b") or []
                    if not (tenkan and kijun and sa and sb):
                        return "neutral", False
                    up_cloud = max(sa[-1], sb[-1])
                    dn_cloud = min(sa[-1], sb[-1])
                    price = closes[-1]
                    if price > up_cloud:
                        sig = "buy"
                    elif price < dn_cloud:
                        sig = "sell"
                    else:
                        # TK cross check on last K
                        sig = "neutral"
                        for i in range(1, min(K, len(tenkan) - 1, len(kijun) - 1) + 1):
                            t_prev, k_prev = tenkan[-(i + 1)], kijun[-(i + 1)]
                            t_curr, k_curr = tenkan[-i], kijun[-i]
                            if t_prev <= k_prev and t_curr > k_curr:
                                sig = "buy"
                                break
                            if t_prev >= k_prev and t_curr < k_curr:
                                sig = "sell"
                                break
                        if sig == "neutral":
                            # Cloud color
                            if sa[-1] > sb[-1]:
                                sig = "buy"
                            elif sa[-1] < sb[-1]:
                                sig = "sell"
                    # New if TK cross or cloud breakout in last K
                    is_new = False
                    # TK cross
                    for i in range(1, min(K, len(tenkan) - 1, len(kijun) - 1) + 1):
                        t_prev, k_prev = tenkan[-(i + 1)], kijun[-(i + 1)]
                        t_curr, k_curr = tenkan[-i], kijun[-i]
                        if (t_prev <= k_prev and t_curr > k_curr) or (t_prev >= k_prev and t_curr < k_curr):
                            is_new = True
                            break
                    if not is_new:
                        # Cloud breakout
                        for i in range(1, min(K, len(sa), len(sb), len(closes)) + 1):
                            up_c = max(sa[-i], sb[-i])
                            dn_c = min(sa[-i], sb[-i])
                            pr = closes[-i]
                            if pr > up_c or pr < dn_c:
                                is_new = True
                                break
                    return sig, is_new

                # Evaluate each indicator cell
                per_tf_sum = 0.0
                sig, is_new = ema_signal_from_recent(ema_recent_21, "EMA21")
                per_tf_sum += score_cell(sig, is_new, "EMA21") * ind_weights["EMA21"]
                sig, is_new = ema_signal_from_recent(ema_recent_50, "EMA50")
                per_tf_sum += score_cell(sig, is_new, "EMA50") * ind_weights["EMA50"]
                sig, is_new = ema_signal_from_recent(ema_recent_200, "EMA200")
                per_tf_sum += score_cell(sig, is_new, "EMA200") * ind_weights["EMA200"]
                sig, is_new = macd_signal_from_recent()
                per_tf_sum += score_cell(sig, is_new, "MACD") * ind_weights["MACD"]
                sig, is_new = rsi_signal_from_recent()
                per_tf_sum += score_cell(sig, is_new, "RSI") * ind_weights["RSI"]
                sig, is_new = utbot_signal()
                per_tf_sum += score_cell(sig, is_new, "UTBOT") * ind_weights["UTBOT"]
                sig, is_new = ichimoku_signal()
                per_tf_sum += score_cell(sig, is_new, "ICHIMOKU") * ind_weights["ICHIMOKU"]

                raw += per_tf_sum * w_tf

            final = 100.0 * (raw / 1.25)
            final = clamp(final, -100.0, 100.0)
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
