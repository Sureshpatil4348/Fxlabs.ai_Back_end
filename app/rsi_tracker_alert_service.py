import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

import aiohttp
import logging

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .email_service import email_service
from .concurrency import pair_locks
from .alert_logging import log_debug, log_info, log_warning, log_error
from .constants import RSI_SUPPORTED_SYMBOLS
from .indicator_cache import indicator_cache


configure_logging()
logger = logging.getLogger(__name__)


class RSITrackerAlertService:
    """
    Simplified RSI Tracker Alert service (closed-bar RSI only).

    - One alert per user (enforced at DB via unique user_id).
    - Each alert has: timeframe (single), rsi_period, overbought, oversold, is_active.
    - Pairs to evaluate are taken from the alert record's optional `pairs` array; if not
      present, fallback to env `RSI_TRACKER_DEFAULT_PAIRS` (comma-separated symbols).
    - Trigger on threshold crossings at closed candles with threshold-level re-arm per side.
    - Per (alert, symbol, timeframe, side) cooldown in minutes to avoid rapid repeats.
    - Log triggers to Supabase `rsi_tracker_alert_triggers` and optionally email the user.
    """

    def __init__(self) -> None:
        self.supabase_url = os.environ.get("SUPABASE_URL", "")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        # Pair-level cooldowns were removed per product decision; rely on threshold re-arm only
        # Hysteresis arm/disarm state per (alert, symbol, timeframe)
        self._hysteresis_map: Dict[str, Dict[str, bool]] = {}
        # Track last evaluated closed bar per (alert_id, symbol, timeframe)
        self._last_closed_bar_ts: Dict[str, int] = {}

    def _normalize_timeframe(self, timeframe: str) -> str:
        """Enforce minimum timeframe of 5M for alerts."""
        if timeframe == "1M":
            return "5M"
        return timeframe

    def _tf_seconds(self, timeframe: str) -> int:
        mapping = {
            "5M": 5 * 60,
            "15M": 15 * 60,
            "30M": 30 * 60,
            "1H": 60 * 60,
            "4H": 4 * 60 * 60,
            "1D": 24 * 60 * 60,
            "1W": 7 * 24 * 60 * 60,
        }
        return mapping.get(timeframe, 5 * 60)

    def _discover_symbols(self) -> List[str]:
        """Return fixed, supported symbols for RSI tracking (broker-suffixed)."""
        return RSI_SUPPORTED_SYMBOLS

    def _is_stale_market(self, market_data: Dict[str, Any], timeframe: str) -> bool:
        try:
            ts_iso = market_data.get("timestamp")
            if not ts_iso:
                return False
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            stale = age > 2 * self._tf_seconds(timeframe)
            if stale:
                log_debug(
                    logger,
                    "market_data_stale",
                    symbol=market_data.get("symbol"),
                    timeframe=timeframe,
                    age_seconds=age,
                    ts=ts_iso,
                )
            return stale
        except Exception:
            return False

    async def _get_last_closed_bar_ts(self, symbol: str, timeframe: str) -> Optional[int]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            tf_map = {
                "5M": MT5Timeframe.M5,
                "15M": MT5Timeframe.M15,
                "30M": MT5Timeframe.M30,
                "1H": MT5Timeframe.H1,
                "4H": MT5Timeframe.H4,
                "1D": MT5Timeframe.D1,
                "1W": MT5Timeframe.W1,
            }
            mt5_tf = tf_map.get(timeframe)
            if not mt5_tf:
                return None
            bars = get_ohlc_data(symbol, mt5_tf, 3)
            for bar in reversed(bars):
                if getattr(bar, "is_closed", None) is not False:
                    return int(bar.time)
            return None
        except Exception:
            return None

    async def _get_recent_rsi_series(self, symbol: str, timeframe: str, period: int, bars_needed: int) -> Optional[List[float]]:
        """Fetch recent closed-bar RSI values from the indicator cache.

        No on-the-fly recomputation; if cache is not warm, return None.
        """
        try:
            # Read last N RSI points from cache; return values only in chronological order
            recent = await indicator_cache.get_recent_rsi(symbol, timeframe, int(period), int(max(bars_needed, 1)))
            if not recent:
                return None
            return [float(v) for (_ts, v) in recent]
        except Exception:
            return None

    async def _detect_rsi_crossing(
        self,
        alert_id: str,
        symbol: str,
        timeframe: str,
        period: int,
        overbought: int,
        oversold: int,
    ) -> Optional[str]:
        """Return one of: "overbought" | "oversold" | None for current closed bar."""
        try:
            series = await self._get_recent_rsi_series(symbol, timeframe, period, bars_needed=3)
            if not series or len(series) < 2:
                log_debug(
                    logger,
                    "rsi_insufficient_data",
                    alert_id=alert_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    period=period,
                    series_len=len(series) if series else 0,
                )
                return None
            key = f"{alert_id}:{symbol}:{timeframe}"
            st = self._hysteresis_map.setdefault(key, {"armed_overbought": True, "armed_oversold": True})
            prev_val = series[-2]
            curr_val = series[-1]

            # Re-arm when RSI crosses back across thresholds
            if not st["armed_overbought"] and curr_val < overbought:
                st["armed_overbought"] = True
                log_debug(
                    logger,
                    "rsi_rearm_overbought",
                    alert_id=alert_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    threshold=overbought,
                    curr_rsi=round(float(curr_val), 2),
                )
            if not st["armed_oversold"] and curr_val > oversold:
                st["armed_oversold"] = True
                log_debug(
                    logger,
                    "rsi_rearm_oversold",
                    alert_id=alert_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    threshold=oversold,
                    curr_rsi=round(float(curr_val), 2),
                )

            if st["armed_overbought"] and prev_val < overbought and curr_val >= overbought:
                st["armed_overbought"] = False
                log_info(
                    logger,
                    "rsi_cross_overbought",
                    alert_id=alert_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    period=period,
                    threshold=overbought,
                    prev_rsi=round(float(prev_val), 2),
                    curr_rsi=round(float(curr_val), 2),
                )
                return "overbought"
            if st["armed_oversold"] and prev_val > oversold and curr_val <= oversold:
                st["armed_oversold"] = False
                log_info(
                    logger,
                    "rsi_cross_oversold",
                    alert_id=alert_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    period=period,
                    threshold=oversold,
                    prev_rsi=round(float(prev_val), 2),
                    curr_rsi=round(float(curr_val), 2),
                )
                return "oversold"
            # No crossing; emit a concise debug reason
            reason = "no_cross"
            details: Dict[str, Any] = {
                "alert_id": alert_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "period": period,
                "overbought": overbought,
                "oversold": oversold,
                "prev_rsi": round(float(prev_val), 2),
                "curr_rsi": round(float(curr_val), 2),
                "armed_overbought": st.get("armed_overbought", True),
                "armed_oversold": st.get("armed_oversold", True),
            }
            if not st.get("armed_overbought", True) and curr_val >= overbought:
                reason = "disarmed_overbought"
            elif not st.get("armed_oversold", True) and curr_val <= oversold:
                reason = "disarmed_oversold"
            elif prev_val >= overbought and curr_val >= overbought:
                reason = "already_overbought"
            elif prev_val <= oversold and curr_val <= oversold:
                reason = "already_oversold"
            elif (prev_val < overbought and curr_val < overbought) and (prev_val > oversold and curr_val > oversold):
                reason = "within_neutral_band"
            log_debug(logger, "rsi_no_trigger", reason=reason, **details)
            return None
        except Exception as e:
            log_error(
                logger,
                "rsi_cross_error",
                alert_id=alert_id,
                symbol=symbol,
                timeframe=timeframe,
                error=str(e),
            )
            return None

    # Pair-level cooldown removed

    async def _get_market_data_for_symbol(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            import MetaTrader5 as mt5  # type: ignore
            tf_map = {
                "5M": MT5Timeframe.M5,
                "15M": MT5Timeframe.M15,
                "30M": MT5Timeframe.M30,
                "1H": MT5Timeframe.H1,
                "4H": MT5Timeframe.H4,
                "1D": MT5Timeframe.D1,
                "1W": MT5Timeframe.W1,
            }
            mt5_tf = tf_map.get(timeframe)
            if mt5_tf:
                ohlc = get_ohlc_data(symbol, mt5_tf, 1)
                if ohlc:
                    bar = ohlc[-1]
                    tick = mt5.symbol_info_tick(symbol)
                    data = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "timestamp": bar.time_iso,
                        "bid": getattr(tick, "bid", None) if tick else None,
                        "ask": getattr(tick, "ask", None) if tick else None,
                        "data_source": "MT5_REAL",
                    }
                    log_debug(
                        logger,
                        "market_data_loaded",
                        symbol=symbol,
                        timeframe=timeframe,
                        source="MT5_REAL",
                    )
                    return data
        except Exception:
            pass
        # No fallback: require real MT5 data
        return None

    async def _log_trigger(self, alert_id: str, symbol: str, timeframe: str, rsi_value: float, trigger_condition: str) -> None:
        if not self.supabase_url or not self.supabase_service_key:
            return
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.supabase_url}/rest/v1/rsi_tracker_alert_triggers"
            payload = {
                "alert_id": alert_id,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "trigger_condition": trigger_condition,
                "symbol": symbol,
                "timeframe": timeframe,
                "rsi_value": round(float(rsi_value), 2),
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
                            timeframe=timeframe,
                            trigger_condition=trigger_condition,
                        )
                    else:
                        log_info(
                            logger,
                            "db_trigger_logged",
                            alert_id=alert_id,
                            symbol=symbol,
                            timeframe=timeframe,
                            trigger_condition=trigger_condition,
                            rsi_value=round(float(rsi_value), 2),
                        )
        except Exception as e:
            log_error(
                logger,
                "db_trigger_log_error",
                alert_id=alert_id,
                symbol=symbol,
                timeframe=timeframe,
                trigger_condition=trigger_condition,
                error=str(e),
            )

    async def _send_notification(self, user_email: str, alert_name: str, triggered_pairs: List[Dict[str, Any]], alert_config: Dict[str, Any]) -> None:
        if not user_email:
            return
        try:
            logger.info(
                f"📧 Scheduling RSI Tracker email -> user={user_email}, alert={alert_name}, pairs={len(triggered_pairs)}"
            )
            await email_service.send_rsi_alert(
                user_email=user_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config,
            )
        except Exception as e:
            logger.error(f"Error sending RSI tracker alert email: {e}")

    async def check_rsi_tracker_alerts(self) -> List[Dict[str, Any]]:
        """Evaluate all active RSI Tracker alerts on closed bars.

        Returns a list of trigger payloads (for observability/testing).
        """
        try:
            # Event-driven paths should not force cache refresh; use snapshot to avoid blocking
            all_alerts = await alert_cache.get_all_alerts_snapshot()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "rsi_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    alert_name = alert.get("alert_name", "RSI Tracker Alert")
                    user_email = alert.get("user_email", "")
                    timeframe = self._normalize_timeframe(alert.get("timeframe", "1H"))
                    # Enforce RSI(14)
                    rsi_period = 14
                    rsi_overbought = int(alert.get("rsi_overbought", alert.get("rsi_overbought_threshold", 70)))
                    rsi_oversold = int(alert.get("rsi_oversold", alert.get("rsi_oversold_threshold", 30)))
                    # Start-of-alert evaluation log (no per-alert pairs, fixed set used)
                    log_debug(
                        logger,
                        "alert_eval_start",
                        alert_type="rsi_tracker",
                        alert_id=alert_id,
                        alert_name=alert_name,
                        user_email=user_email,
                        timeframe=timeframe,
                        rsi_period=rsi_period,
                        rsi_overbought=rsi_overbought,
                        rsi_oversold=rsi_oversold,
                    )
                    # Also log a concise INFO-level config line per request
                    log_info(
                        logger,
                        "alert_eval_config",
                        alert_type="rsi_tracker",
                        alert_id=alert_id,
                        user_email=user_email,
                        timeframe=timeframe,
                        rsi_period=rsi_period,
                        rsi_overbought=rsi_overbought,
                        rsi_oversold=rsi_oversold,
                    )
                    # Pairs: auto-discover from env/global list. Ignore per-alert pairs.
                    pairs: List[str] = self._discover_symbols()

                    # Enforce closed-bar policy: evaluate once per closed bar per symbol/timeframe
                    triggered_pairs: List[Dict[str, Any]] = []
                    for symbol in pairs:
                        key = f"{symbol}:{timeframe}"
                        bar_key = f"{alert_id}:{symbol}:{timeframe}"
                        async with pair_locks.acquire(key):
                            market = await self._get_market_data_for_symbol(symbol, timeframe)
                            if not market:
                                log_warning(
                                    logger,
                                    "market_data_missing",
                                    symbol=symbol,
                                    timeframe=timeframe,
                                )
                                continue
                            if self._is_stale_market(market, timeframe):
                                continue
                            last_ts = await self._get_last_closed_bar_ts(symbol, timeframe)
                            if last_ts is None:
                                log_debug(
                                    logger,
                                    "closed_bar_unknown",
                                    symbol=symbol,
                                    timeframe=timeframe,
                                )
                                continue
                            prev_ts = self._last_closed_bar_ts.get(bar_key)
                            # Startup warm-up: if we've never seen this (symbol,timeframe) key,
                            # baseline the last closed bar and skip triggering on this first observation.
                            if prev_ts is None:
                                self._last_closed_bar_ts[bar_key] = last_ts
                                continue
                            if prev_ts is not None and prev_ts == last_ts:
                                # Already evaluated this closed bar for this alert/user
                                log_debug(
                                    logger,
                                    "closed_bar_already_evaluated",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    last_ts=last_ts,
                                )
                                continue
                            self._last_closed_bar_ts[bar_key] = last_ts

                            # Detect crossing
                            cond = await self._detect_rsi_crossing(
                                alert_id=alert_id,
                                symbol=symbol,
                                timeframe=timeframe,
                                period=rsi_period,
                                overbought=rsi_overbought,
                                oversold=rsi_oversold,
                            )
                            if not cond:
                                continue

                            # No pair-level cooldown; rely on threshold re-arm only

                            # Compute RSI value again for payload (last of series)
                            series = await self._get_recent_rsi_series(symbol, timeframe, rsi_period, bars_needed=1)
                            rsi_val = series[-1] if series else None
                            if rsi_val is None:
                                continue

                            item = {
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "rsi_value": round(float(rsi_val), 2),
                                "trigger_condition": cond,
                                "current_price": market.get("close", 0),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                            triggered_pairs.append(item)
                            # Fire-and-forget DB log
                            asyncio.create_task(self._log_trigger(alert_id, symbol, timeframe, item["rsi_value"], cond))

                    if triggered_pairs:
                        log_info(
                            logger,
                            "rsi_tracker_triggers",
                            alert_id=alert_id,
                            alert_name=alert_name,
                            user_email=user_email,
                            count=len(triggered_pairs),
                        )
                        payload = {
                            "alert_id": alert_id,
                            "alert_name": alert_name,
                            "user_email": user_email,
                            "triggered_pairs": triggered_pairs,
                            "alert_config": alert,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                        }
                        triggers.append(payload)
                        # Send email if configured (default on)
                        methods = alert.get("notification_methods") or ["email"]
                        if "email" in methods:
                            log_info(
                                logger,
                                "email_queue",
                                alert_type="rsi_tracker",
                                alert_id=alert_id,
                            )
                            asyncio.create_task(self._send_notification(user_email, alert_name, triggered_pairs, alert))
                        else:
                            log_info(
                                logger,
                                "email_disabled",
                                alert_type="rsi_tracker",
                                alert_id=alert_id,
                                methods=methods,
                            )
                    # End-of-alert evaluation log
                    log_debug(
                        logger,
                        "alert_eval_end",
                        alert_type="rsi_tracker",
                        alert_id=alert_id,
                        alert_name=alert_name,
                        triggered_count=len(triggered_pairs),
                    )

            return triggers
        except Exception as e:
            log_error(
                logger,
                "rsi_tracker_check_error",
                error=str(e),
            )
            return []


# Global instance
rsi_tracker_alert_service = RSITrackerAlertService()
