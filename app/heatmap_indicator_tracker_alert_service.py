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
        # Supabase creds for trigger logging
        self.supabase_url = os.environ.get("SUPABASE_URL", "")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    def _normalize_timeframe(self, timeframe: str) -> str:
        """Enforce minimum timeframe of 5M for alerts."""
        if timeframe == "1M":
            return "5M"
        return timeframe

    def _key(self, alert_id: str, symbol: str, timeframe: str, indicator: str) -> str:
        return f"{alert_id}:{symbol}:{timeframe}:{indicator}"

    async def check_heatmap_indicator_tracker_alerts(self) -> List[Dict[str, Any]]:
        try:
            all_alerts = await alert_cache.get_all_alerts()
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

                    ts_iso = datetime.now(timezone.utc).isoformat()
                    per_alert_triggers: List[Dict[str, Any]] = []
                    for symbol in pairs:
                        async with pair_locks.acquire(self._key(alert_id, symbol, timeframe, indicator)):
                            signal = await self._compute_indicator_signal(symbol, timeframe, indicator)
                            if signal not in ("buy", "sell", "neutral"):
                                continue
                            k = self._key(alert_id, symbol, timeframe, indicator)
                            prev = self._last_signal.get(k)
                            if prev is None:
                                # Startup warm-up: baseline last signal and skip first observation
                                self._last_signal[k] = signal
                                log_debug(
                                    logger,
                                    "indicator_baseline",
                                    alert_id=alert_id,
                                    symbol=symbol,
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
                                symbol=symbol,
                                timeframe=timeframe,
                                indicator=indicator,
                                signal=signal,
                                previous=prev,
                            )
                            if signal in ("buy", "sell") and signal != prev:
                                per_alert_triggers.append({
                                    "symbol": symbol,
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
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    indicator=indicator,
                                    trigger=signal,
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
                        # Fire-and-forget DB trigger logs
                        for item in per_alert_triggers:
                            asyncio.create_task(self._log_trigger(
                                alert_id=alert_id,
                                symbol=item.get("symbol", ""),
                                timeframe=item.get("timeframe", ""),
                                indicator=item.get("indicator", ""),
                                signal=item.get("trigger_condition", ""),
                            ))
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

    async def _log_trigger(
        self,
        alert_id: str,
        symbol: str,
        timeframe: str,
        indicator: str,
        signal: str,
    ) -> None:
        if not self.supabase_url or not self.supabase_service_key:
            return
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.supabase_url}/rest/v1/heatmap_indicator_tracker_alert_triggers"
            payload = {
                "alert_id": alert_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "indicator": indicator,
                "signal": signal,
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
                            timeframe=timeframe,
                            indicator=indicator,
                            signal=signal,
                        )
                    else:
                        log_info(
                            logger,
                            "db_trigger_logged",
                            alert_id=alert_id,
                            symbol=symbol,
                            timeframe=timeframe,
                            indicator=indicator,
                            signal=signal,
                        )
        except Exception as e:
            log_error(
                logger,
                "db_trigger_log_error",
                alert_id=alert_id,
                symbol=symbol,
                timeframe=timeframe,
                indicator=indicator,
                signal=signal,
                error=str(e),
            )

    async def _compute_indicator_signal(self, symbol: str, timeframe: str, indicator: str) -> str:
        try:
            # Placeholder rules; plug in real computations as needed
            if indicator in ("ema21", "ema50", "ema200"):
                # Simplified: flip to buy or sell randomly per call (deterministic seed)
                import random
                seed = sum(ord(c) for c in (symbol + timeframe + indicator)) % 1000
                random.seed(seed)
                return random.choice(["neutral", "buy", "sell"])
            if indicator == "macd":
                return "buy"
            if indicator == "rsi":
                return "sell"
            if indicator == "utbot":
                return "neutral"
            if indicator == "ichimokuclone":
                return "buy"
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


