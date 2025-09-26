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
        # Pair-level cooldowns per side
        self.pair_cooldown_minutes_default: int = int(os.environ.get("RSI_TRACKER_COOLDOWN_MINUTES", "30"))
        self._pair_cooldowns: Dict[str, datetime] = {}
        # Hysteresis arm/disarm state per (alert, symbol, timeframe)
        self._hysteresis_map: Dict[str, Dict[str, bool]] = {}
        # Track last evaluated closed bar per (symbol, timeframe)
        self._last_closed_bar_ts: Dict[str, int] = {}

    def _tf_seconds(self, timeframe: str) -> int:
        mapping = {
            "1M": 60,
            "5M": 5 * 60,
            "15M": 15 * 60,
            "30M": 30 * 60,
            "1H": 60 * 60,
            "4H": 4 * 60 * 60,
            "1D": 24 * 60 * 60,
            "1W": 7 * 24 * 60 * 60,
        }
        return mapping.get(timeframe, 60)

    def _is_stale_market(self, market_data: Dict[str, Any], timeframe: str) -> bool:
        try:
            ts_iso = market_data.get("timestamp")
            if not ts_iso:
                return False
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return age > 2 * self._tf_seconds(timeframe)
        except Exception:
            return False

    async def _get_last_closed_bar_ts(self, symbol: str, timeframe: str) -> Optional[int]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            tf_map = {
                "1M": MT5Timeframe.M1,
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
            bars = get_ohlc_data(symbol, mt5_tf, 2)
            if not bars:
                return None
            return int(bars[-1].time)
        except Exception:
            return None

    async def _get_recent_rsi_series(self, symbol: str, timeframe: str, period: int, bars_needed: int) -> Optional[List[float]]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            tf_map = {
                "1M": MT5Timeframe.M1,
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
            count = max(period + bars_needed + 2, period + 5)
            ohlc_data = get_ohlc_data(symbol, mt5_tf, count)
            if not ohlc_data or len(ohlc_data) < period + 1:
                return None
            closes = [bar.close for bar in ohlc_data]
            series = self._calculate_rsi_series(closes, period)
            if not series:
                return None
            return series[-bars_needed:] if len(series) >= bars_needed else series
        except Exception:
            return None

    def _calculate_rsi_series(self, closes: List[float], period: int) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsis: List[float] = []
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100 - (100 / (1 + rs)))

        return rsis

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
                return None
            key = f"{alert_id}:{symbol}:{timeframe}"
            st = self._hysteresis_map.setdefault(key, {"armed_overbought": True, "armed_oversold": True})
            prev_val = series[-2]
            curr_val = series[-1]

            # Re-arm when RSI crosses back across thresholds
            if not st["armed_overbought"] and curr_val < overbought:
                st["armed_overbought"] = True
            if not st["armed_oversold"] and curr_val > oversold:
                st["armed_oversold"] = True

            if st["armed_overbought"] and prev_val < overbought and curr_val >= overbought:
                st["armed_overbought"] = False
                return "overbought"
            if st["armed_oversold"] and prev_val > oversold and curr_val <= oversold:
                st["armed_oversold"] = False
                return "oversold"
            return None
        except Exception as e:
            logger.error(f"Error detecting RSI crossing: {e}")
            return None

    def _allow_by_pair_cooldown(self, alert_id: str, symbol: str, timeframe: str, side: str, cooldown_minutes: Optional[int]) -> bool:
        try:
            minutes = int(cooldown_minutes) if cooldown_minutes is not None else self.pair_cooldown_minutes_default
        except Exception:
            minutes = self.pair_cooldown_minutes_default
        key = f"{alert_id}:{symbol}:{timeframe}:{side}"
        now = datetime.now(timezone.utc)
        last = self._pair_cooldowns.get(key)
        if last is not None and (now - last) < timedelta(minutes=minutes):
            return False
        self._pair_cooldowns[key] = now
        return True

    async def _get_market_data_for_symbol(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            import MetaTrader5 as mt5  # type: ignore
            tf_map = {
                "1M": MT5Timeframe.M1,
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
                    return {
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
        except Exception:
            pass
        # Fallback simulated data
        try:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "open": 1.1000,
                "high": 1.1010,
                "low": 1.0990,
                "close": 1.1005,
                "volume": 1000,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_source": "SIMULATED",
            }
        except Exception:
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
                        logger.error(f"Failed to log RSI tracker trigger: {resp.status} - {txt}")
        except Exception as e:
            logger.error(f"Error logging RSI tracker trigger: {e}")

    async def _send_notification(self, user_email: str, alert_name: str, triggered_pairs: List[Dict[str, Any]], alert_config: Dict[str, Any]) -> None:
        if not user_email:
            return
        try:
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
            all_alerts = await alert_cache.get_all_alerts()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "rsi_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    alert_name = alert.get("alert_name", "RSI Tracker Alert")
                    user_email = alert.get("user_email", "")
                    timeframe = alert.get("timeframe", "1H")
                    rsi_period = int(alert.get("rsi_period", 14))
                    rsi_overbought = int(alert.get("rsi_overbought", alert.get("rsi_overbought_threshold", 70)))
                    rsi_oversold = int(alert.get("rsi_oversold", alert.get("rsi_oversold_threshold", 30)))
                    cooldown_minutes = alert.get("cooldown_minutes")  # optional, default inside service
                    # Pairs: use record if present; else fallback to env default list
                    pairs: List[str] = alert.get("pairs", []) or []
                    if not pairs:
                        env_pairs = os.environ.get("RSI_TRACKER_DEFAULT_PAIRS", "")
                        if env_pairs:
                            pairs = [p.strip() for p in env_pairs.split(",") if p.strip()]

                    # Enforce closed-bar policy: evaluate once per closed bar per symbol/timeframe
                    triggered_pairs: List[Dict[str, Any]] = []
                    for symbol in pairs:
                        key = f"{symbol}:{timeframe}"
                        async with pair_locks.acquire(key):
                            market = await self._get_market_data_for_symbol(symbol, timeframe)
                            if not market or self._is_stale_market(market, timeframe):
                                continue
                            last_ts = await self._get_last_closed_bar_ts(symbol, timeframe)
                            if last_ts is None:
                                continue
                            prev_ts = self._last_closed_bar_ts.get(key)
                            if prev_ts is not None and prev_ts == last_ts:
                                continue
                            self._last_closed_bar_ts[key] = last_ts

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

                            # Per-side cooldown
                            side = "overbought" if cond == "overbought" else "oversold"
                            if not self._allow_by_pair_cooldown(alert_id, symbol, timeframe, side, cooldown_minutes):
                                continue

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
                            asyncio.create_task(self._send_notification(user_email, alert_name, triggered_pairs, alert))

            return triggers
        except Exception as e:
            logger.error(f"Error checking RSI tracker alerts: {e}")
            return []


# Global instance
rsi_tracker_alert_service = RSITrackerAlertService()


