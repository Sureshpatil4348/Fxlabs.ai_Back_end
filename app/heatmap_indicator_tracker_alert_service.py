import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

import logging

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .email_service import email_service
from .concurrency import pair_locks
from .alert_logging import log_debug, log_info, log_warning, log_error
from .rsi_utils import calculate_rsi_series, closed_closes
from .indicator_cache import indicator_cache


configure_logging()
logger = logging.getLogger(__name__)


class HeatmapIndicatorTrackerAlertService:
    """
    Custom Indicator Tracker Alert service (single alert per user):
    Emits triggers when the selected indicator flips to buy/sell on the chosen timeframe for any selected pair.
    """

    def __init__(self) -> None:
        # Last signal per (alert, symbol, timeframe, indicator)
        self._last_signal: Dict[str, str] = {}
        # Supabase creds for trigger logging (tenant-aware)
        from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY
        self.supabase_url = SUPABASE_URL
        self.supabase_service_key = SUPABASE_SERVICE_KEY

    def _normalize_timeframe(self, timeframe: str) -> str:
        """Enforce minimum timeframe of 5M for alerts."""
        if timeframe == "1M":
            return "5M"
        return timeframe

    def _key(self, alert_id: str, symbol: str, timeframe: str, indicator: str) -> str:
        return f"{alert_id}:{symbol}:{timeframe}:{indicator}"

    async def check_heatmap_indicator_tracker_alerts(self) -> List[Dict[str, Any]]:
        try:
            # Event-driven paths should not force cache refresh; use snapshot to avoid blocking
            all_alerts = await alert_cache.get_all_alerts_snapshot()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "heatmap_indicator_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    user_email = alert.get("user_email", "")
                    timeframe = self._normalize_timeframe(alert.get("timeframe", "1H"))
                    indicator = (alert.get("indicator") or "ema21").lower()
                    pairs: List[str] = alert.get("pairs", []) or []
                    # Start-of-alert evaluation log
                    log_debug(
                        logger,
                        "alert_eval_start",
                        alert_type="indicator_tracker",
                        alert_id=alert_id,
                        user_email=user_email,
                        timeframe=timeframe,
                        indicator=indicator,
                        pairs=len(pairs),
                    )
                    # INFO-level concise config
                    log_info(
                        logger,
                        "alert_eval_config",
                        alert_type="indicator_tracker",
                        alert_id=alert_id,
                        user_email=user_email,
                        timeframe=timeframe,
                        indicator=indicator,
                        pairs=len(pairs),
                    )

                    ts_iso = datetime.now(timezone.utc).isoformat()
                    from .mt5_utils import canonicalize_symbol
                    from .constants import RSI_SUPPORTED_SYMBOLS

                    per_alert_triggers: List[Dict[str, Any]] = []
                    for input_symbol in pairs:
                        # Canonicalize symbol and auto-append broker suffix when missing
                        symbol_canon = canonicalize_symbol(input_symbol)
                        if symbol_canon not in RSI_SUPPORTED_SYMBOLS and (symbol_canon + "m") in RSI_SUPPORTED_SYMBOLS:
                            symbol_canon = symbol_canon + "m"
                        async with pair_locks.acquire(self._key(alert_id, symbol_canon, timeframe, indicator)):
                            signal = await self._compute_indicator_signal(symbol_canon, timeframe, indicator)
                            if signal not in ("buy", "sell", "neutral"):
                                continue
                            k = self._key(alert_id, symbol_canon, timeframe, indicator)
                            prev = self._last_signal.get(k)
                            if prev is None:
                                # Startup warm-up: baseline last signal and skip first observation
                                self._last_signal[k] = signal
                                log_debug(
                                    logger,
                                    "indicator_baseline",
                                    alert_id=alert_id,
                                    symbol=symbol_canon,
                                    input_symbol=input_symbol,
                                    timeframe=timeframe,
                                    indicator=indicator,
                                    baseline_signal=signal,
                                )
                                continue
                            self._last_signal[k] = signal
                            log_debug(
                                logger,
                                "indicator_signal",
                                alert_id=alert_id,
                                symbol=symbol_canon,
                                input_symbol=input_symbol,
                                timeframe=timeframe,
                                indicator=indicator,
                                signal=signal,
                                previous=prev,
                            )
                            if signal in ("buy", "sell") and signal != prev:
                                per_alert_triggers.append({
                                    "symbol": symbol_canon,
                                    "timeframe": timeframe,
                                    "indicator": indicator,
                                    "trigger_condition": signal,
                                    "current_price": None,
                                    "timestamp": ts_iso,
                                })
                                log_info(
                                    logger,
                                    "indicator_tracker_trigger",
                                    alert_id=alert_id,
                                    symbol=symbol_canon,
                                    timeframe=timeframe,
                                    indicator=indicator,
                                    trigger=signal,
                                )
                            else:
                                # No trigger; log concise reason
                                reason = "neutral_signal" if signal == "neutral" else "no_flip"
                                log_debug(
                                    logger,
                                    "indicator_no_trigger",
                                    alert_id=alert_id,
                                    symbol=symbol_canon,
                                    timeframe=timeframe,
                                    indicator=indicator,
                                    signal=signal,
                                    previous=prev,
                                    reason=reason,
                                )

                    if per_alert_triggers:
                        payload = {
                            "alert_id": alert_id,
                            "alert_name": alert.get("alert_name", "Indicator Tracker Alert"),
                            "user_email": user_email,
                            "triggered_pairs": per_alert_triggers,
                            "alert_config": alert,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                        }
                        triggers.append(payload)
                        # DB trigger logging removed per product decision
                        methods = alert.get("notification_methods") or ["email"]
                        if "email" in methods:
                            log_info(
                                logger,
                                "email_queue",
                                alert_type="indicator_tracker",
                                alert_id=alert_id,
                            )
                            asyncio.create_task(self._send_email(user_email, payload))
                        else:
                            log_info(
                                logger,
                                "email_disabled",
                                alert_type="indicator_tracker",
                                alert_id=alert_id,
                                methods=methods,
                            )
                    # End-of-alert evaluation log
                    log_debug(
                        logger,
                        "alert_eval_end",
                        alert_type="indicator_tracker",
                        alert_id=alert_id,
                        triggered_count=len(per_alert_triggers),
                    )

            return triggers
        except Exception as e:
            logger.error(f"Error checking Indicator Tracker alerts: {e}")
            return []

    # DB trigger logging removed

    async def _compute_indicator_signal(self, symbol: str, timeframe: str, indicator: str) -> str:
        """Compute indicator signal using cache-first reads over closed bars.

        Supported:
        - ema21/ema50/ema200: cross of close vs EMA -> buy/sell; otherwise neutral (cache)
        - macd: cross of MACD vs signal with zero-line agreement -> buy/sell; otherwise neutral (cache)
        - rsi: crossing of RSI(14) vs 50 -> buy/sell; otherwise neutral (cache with warm-up fallback)
        Unknown indicators return neutral.
        """
        try:
            from .models import Timeframe as TF
            from .mt5_utils import get_ohlc_data

            # K=3 closed-bar lookback window for newness/cross checks
            K = 3

            tf_map = {
                "5M": TF.M5,
                "15M": TF.M15,
                "30M": TF.M30,
                "1H": TF.H1,
                "4H": TF.H4,
                "1D": TF.D1,
                "1W": TF.W1,
            }
            mtf = tf_map.get(timeframe)
            if not mtf:
                return "neutral"

            ind = (indicator or "").lower()

            # Fetch recent closed OHLC to align time-based series from cache
            bars = get_ohlc_data(symbol, mtf, 300)
            if not bars:
                return "neutral"
            closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
            if len(closed_bars) < 5:
                return "neutral"
            closes = [float(b.close) for b in closed_bars]
            ts_to_close: Dict[int, float] = {int(b.time): float(b.close) for b in closed_bars}

            # EMA family via cache
            if ind in ("ema21", "ema50", "ema200"):
                try:
                    p = int(ind.replace("ema", ""))
                except Exception:
                    return "neutral"
                if p < 2:
                    return "neutral"
                ema_recent = await indicator_cache.get_recent_ema(symbol, timeframe, p, K + 3)
                if not ema_recent or len(ema_recent) < 2:
                    return "neutral"
                # Align EMA to closes by timestamp
                aligned: List[Tuple[int, float, float]] = []  # (ts, close, ema)
                for ts, ev in ema_recent:
                    c = ts_to_close.get(int(ts))
                    if c is not None:
                        aligned.append((int(ts), float(c), float(ev)))
                if len(aligned) < 2:
                    return "neutral"
                _, c_prev, e_prev = aligned[-2]
                _, c_curr, e_curr = aligned[-1]
                if c_prev <= e_prev and c_curr > e_curr:
                    return "buy"
                if c_prev >= e_prev and c_curr < e_curr:
                    return "sell"
                return "neutral"

            # MACD via cache (12,26,9); return only on fresh cross (prev->curr)
            if ind == "macd":
                macd_recent = await indicator_cache.get_recent_macd(symbol, timeframe, 12, 26, 9, K + 3)
                if not macd_recent or len(macd_recent) < 2:
                    return "neutral"
                _, m_prev, s_prev, _ = macd_recent[-2]
                _, m_curr, s_curr, _ = macd_recent[-1]
                # Require zero-line agreement like Heatmap scoring
                if m_prev <= s_prev and m_curr > s_curr and m_curr > 0:
                    return "buy"
                if m_prev >= s_prev and m_curr < s_curr and m_curr < 0:
                    return "sell"
                return "neutral"

            # RSI via cache (14); warm-up fallback compute from closed OHLC if needed
            if ind == "rsi":
                rsi_recent = await indicator_cache.get_recent_rsi(symbol, timeframe, 14, K + 2)
                if (not rsi_recent) or len(rsi_recent) < 2:
                    # Fallback: compute latest RSI from closed bars, cache it, then evaluate
                    try:
                        closes_closed = closed_closes(closed_bars)  # type: ignore[arg-type]
                        rsis = calculate_rsi_series(closes_closed, 14)
                        if rsis and len(rsis) >= 2:
                            # Update cache with last value to converge ring quickly
                            last_ts = int(closed_bars[-1].time)
                            await indicator_cache.update_rsi(symbol, timeframe, 14, float(rsis[-1]), ts_ms=last_ts)
                            rsi_recent = [(last_ts - 1, float(rsis[-2])), (last_ts, float(rsis[-1]))]
                    except Exception:
                        return "neutral"
                if not rsi_recent or len(rsi_recent) < 2:
                    return "neutral"
                r_prev = float(rsi_recent[-2][1])
                r_curr = float(rsi_recent[-1][1])
                if r_prev <= 50.0 and r_curr > 50.0:
                    return "buy"
                if r_prev >= 50.0 and r_curr < 50.0:
                    return "sell"
                return "neutral"

            # Unknown indicator
            return "neutral"
        except Exception:
            return "neutral"

    async def _send_email(self, user_email: str, payload: Dict[str, Any]) -> None:
        try:
            await email_service.send_custom_indicator_alert(
                user_email=user_email,
                alert_name=payload.get("alert_name", "Indicator Tracker Alert"),
                triggered_pairs=payload.get("triggered_pairs", []),
                alert_config=payload.get("alert_config", {}),
            )
        except Exception as e:
            logger.error(f"Error sending Indicator Tracker email: {e}")


heatmap_indicator_tracker_alert_service = HeatmapIndicatorTrackerAlertService()
