import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import aiohttp
import logging

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .email_service import email_service
from .concurrency import pair_locks


configure_logging()
logger = logging.getLogger(__name__)


class RSICorrelationTrackerAlertService:
    """
    RSI Correlation Tracker Alert service supporting two modes:
      - rsi_threshold: detect transitions into mismatch per doc
      - real_correlation: detect transitions into correlation-based mismatch

    Single alert per user; closed-bar evaluation. Triggers are logged to Supabase and email can be sent (reusing RSI template for simplicity).
    """

    def __init__(self) -> None:
        self.supabase_url = os.environ.get("SUPABASE_URL", "")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        # Remember last mismatch state per (alert, pair_key, timeframe, mode)
        self._last_state: Dict[str, bool] = {}

    def _state_key(self, alert_id: str, pair_key: str, timeframe: str, mode: str) -> str:
        return f"{alert_id}:{pair_key}:{timeframe}:{mode}"

    async def check_rsi_correlation_tracker_alerts(self) -> List[Dict[str, Any]]:
        try:
            all_alerts = await alert_cache.get_all_alerts()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "rsi_correlation_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    user_email = alert.get("user_email", "")
                    timeframe = alert.get("timeframe", "1H")
                    mode = (alert.get("mode") or "rsi_threshold").lower()
                    rsi_period = int(alert.get("rsi_period", 14))
                    rsi_ob = int(alert.get("rsi_overbought", 70))
                    rsi_os = int(alert.get("rsi_oversold", 30))
                    corr_window = int(alert.get("correlation_window", 50))

                    # Pairs for correlation come from env or alert (future). Expect comma pairs like "EURUSD_GBPUSD,USDJPY_GBPUSD"
                    pairs_env = os.environ.get("RSI_CORR_TRACKER_DEFAULT_PAIRS", "")
                    pair_keys: List[str] = [p.strip() for p in pairs_env.split(",") if p.strip()]
                    if not pair_keys:
                        continue

                    for pair_key in pair_keys:
                        parts = pair_key.split("_")
                        if len(parts) != 2:
                            continue
                        s1, s2 = parts[0], parts[1]
                        k = self._state_key(alert_id, pair_key, timeframe, mode)

                        async with pair_locks.acquire(f"{s1}:{timeframe}"):
                            async with pair_locks.acquire(f"{s2}:{timeframe}"):
                                if mode == "rsi_threshold":
                                    mismatch, val = await self._evaluate_rsi_threshold_mismatch(s1, s2, timeframe, rsi_period, rsi_ob, rsi_os)
                                    trig_type = "rsi_mismatch"
                                else:
                                    mismatch, val = await self._evaluate_real_correlation_mismatch(s1, s2, timeframe, corr_window)
                                    trig_type = "real_mismatch"

                        prev = self._last_state.get(k, False)
                        self._last_state[k] = mismatch
                        if (not prev) and mismatch:
                            payload = {
                                "alert_id": alert_id,
                                "alert_name": alert.get("alert_name", "RSI Correlation Tracker Alert"),
                                "user_email": user_email,
                                "triggered_pairs": [{
                                    "symbol": pair_key,
                                    "timeframe": timeframe,
                                    "trigger_condition": trig_type,
                                    "rsi_value": val if isinstance(val, (int, float)) else None,
                                    "current_price": None,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }],
                                "alert_config": alert,
                                "triggered_at": datetime.now(timezone.utc).isoformat(),
                            }
                            triggers.append(payload)
                            asyncio.create_task(self._log_trigger(alert_id, timeframe, mode, trig_type, pair_key, val))
                            # Optional email reusing RSI template (single card); symbol shown as pair_key
                            methods = alert.get("notification_methods") or ["email"]
                            if "email" in methods:
                                asyncio.create_task(self._send_email(user_email, payload))

            return triggers
        except Exception as e:
            logger.error(f"Error checking RSI Correlation Tracker: {e}")
            return []

    async def _evaluate_rsi_threshold_mismatch(self, s1: str, s2: str, timeframe: str, period: int, ob: int, os_: int) -> (bool, Optional[float]):
        try:
            r1 = await self._calculate_rsi_latest(s1, timeframe, period)
            r2 = await self._calculate_rsi_latest(s2, timeframe, period)
            if r1 is None or r2 is None:
                return False, None
            # Positive mismatch: one >= OB and other <= OS
            pos = (r1 >= ob and r2 <= os_) or (r2 >= ob and r1 <= os_)
            # Negative mismatch: both >= OB or both <= OS
            neg = (r1 >= ob and r2 >= ob) or (r1 <= os_ and r2 <= os_)
            return (pos or neg), float((r1 + r2) / 2.0)
        except Exception:
            return False, None

    async def _evaluate_real_correlation_mismatch(self, s1: str, s2: str, timeframe: str, window: int) -> (bool, Optional[float]):
        try:
            corr = await self._calculate_returns_correlation(s1, s2, timeframe, window)
            if corr is None:
                return False, None
            # Thresholds from doc example
            # Positive pairs: correlation < +0.25 -> mismatch (use absolute if sign unknown; here keep rule simple)
            # Negative pairs: correlation > -0.15 -> mismatch
            # Without prior sign classification, we treat mismatch if |corr| < 0.25 (weak/unstable relation)
            mismatch = abs(corr) < 0.25
            return mismatch, float(corr)
        except Exception:
            return False, None

    async def _calculate_rsi_latest(self, symbol: str, timeframe: str, period: int) -> Optional[float]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as TF
            tf_map = {"1M": TF.M1, "5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}
            mtf = tf_map.get(timeframe)
            if not mtf:
                return None
            ohlc = get_ohlc_data(symbol, mtf, period + 10)
            if not ohlc or len(ohlc) < period + 1:
                return None
            closes = [b.close for b in ohlc]
            series = self._rsi_series(closes, period)
            return series[-1] if series else None
        except Exception:
            return None

    def _rsi_series(self, closes: List[float], period: int) -> List[float]:
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

    async def _calculate_returns_correlation(self, s1: str, s2: str, timeframe: str, window: int) -> Optional[float]:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as TF
            tf_map = {"1M": TF.M1, "5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}
            mtf = tf_map.get(timeframe)
            if not mtf:
                return None
            count = max(window + 5, window + 1)
            o1 = get_ohlc_data(s1, mtf, count)
            o2 = get_ohlc_data(s2, mtf, count)
            if not o1 or not o2:
                return None
            c1 = [b.close for b in o1][- (window + 1):]
            c2 = [b.close for b in o2][- (window + 1):]
            n = min(len(c1), len(c2))
            if n < window + 1:
                return None
            c1 = c1[-n:]
            c2 = c2[-n:]
            r1 = [(c1[i] / c1[i - 1] - 1.0) for i in range(1, len(c1))]
            r2 = [(c2[i] / c2[i - 1] - 1.0) for i in range(1, len(c2))]
            m = min(len(r1), len(r2), window)
            if m < 2:
                return None
            r1 = r1[-m:]
            r2 = r2[-m:]
            mean1 = sum(r1) / m
            mean2 = sum(r2) / m
            num = sum((a - mean1) * (b - mean2) for a, b in zip(r1, r2))
            den1 = (sum((a - mean1) ** 2 for a in r1)) ** 0.5
            den2 = (sum((b - mean2) ** 2 for b in r2)) ** 0.5
            if den1 == 0 or den2 == 0:
                return 0.0
            corr = num / (den1 * den2)
            if corr > 1:
                corr = 1.0
            if corr < -1:
                corr = -1.0
            return float(corr)
        except Exception:
            return None

    async def _log_trigger(self, alert_id: str, timeframe: str, mode: str, trigger_type: str, pair_key: str, value: Optional[float]) -> None:
        if not self.supabase_url or not self.supabase_service_key:
            return
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.supabase_url}/rest/v1/rsi_correlation_tracker_alert_triggers"
            payload = {
                "alert_id": alert_id,
                "mode": mode,
                "trigger_type": trigger_type,
                "pair_key": pair_key,
                "timeframe": timeframe,
                "value": value,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status not in (200, 201):
                        txt = await resp.text()
                        logger.error(f"Failed to log RSI correlation trigger: {resp.status} - {txt}")
        except Exception as e:
            logger.error(f"Error logging RSI correlation trigger: {e}")

    async def _send_email(self, user_email: str, payload: Dict[str, Any]) -> None:
        try:
            await email_service.send_rsi_alert(
                user_email=user_email,
                alert_name=payload.get("alert_name", "RSI Correlation Tracker Alert"),
                triggered_pairs=payload.get("triggered_pairs", []),
                alert_config=payload.get("alert_config", {}),
            )
        except Exception as e:
            logger.error(f"Error sending correlation tracker email: {e}")


rsi_correlation_tracker_alert_service = RSICorrelationTrackerAlertService()


