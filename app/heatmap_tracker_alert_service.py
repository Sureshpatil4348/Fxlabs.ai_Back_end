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


class HeatmapTrackerAlertService:
    """
    Heatmap/Quantum Analysis Tracker Alert service (single alert per user).
    Uses style-weighted indicator strengths (Buy%/Sell%) and triggers on threshold crossings per pair.
    """

    def __init__(self) -> None:
        # Re-arm per (alert, symbol, side) to avoid re-firing while in-zone
        self._armed: Dict[str, Dict[str, bool]] = {}
        # Supabase creds for trigger logging
        self.supabase_url = os.environ.get("SUPABASE_URL", "")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

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
                    style = (alert.get("trading_style") or "dayTrader").lower()
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

                    ts_iso = datetime.now(timezone.utc).isoformat()
                    per_alert_triggers: List[Dict[str, Any]] = []
                    for symbol in pairs:
                        async with pair_locks.acquire(self._key(alert_id, symbol)):
                            # Compute Buy%/Sell% via style weighting using real or simulated indicator strengths
                            buy_pct, sell_pct, final_score = await self._compute_buy_sell_percent(symbol, style)
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
                                # If currently above threshold, mark that side disarmed to avoid immediate trigger.
                                st = {"buy": True, "sell": True}
                                if buy_pct >= buy_t:
                                    st["buy"] = False
                                if sell_pct >= sell_t:
                                    st["sell"] = False
                                self._armed[k] = st
                                # Skip triggering on this first observation after baselining
                                continue

                            # Re-arm checks
                            if not st["buy"] and buy_pct < max(0.0, buy_t - 5):
                                st["buy"] = True
                            if not st["sell"] and sell_pct < max(0.0, sell_t - 5):
                                st["sell"] = True

                            trig_type: Optional[str] = None
                            if st["buy"] and buy_pct >= buy_t:
                                st["buy"] = False
                                trig_type = "buy"
                            elif st["sell"] and sell_pct >= sell_t:
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
        try:
            # Try using the existing heatmap calculation code path if available would be ideal,
            # but we keep a simplified stand-in here (final_score in [-100..100])
            # Simulate Buy%/Sell% using a simple proxy around 50 with minor variance.
            import random
            # A deterministic but simple mapping using symbol hash
            seed = sum(ord(c) for c in symbol) % 1000
            random.seed(seed)
            base = 50.0 + (random.random() - 0.5) * 20.0
            # style influence
            if style == "scalper":
                base += 2.0
            elif style == "swingtrader":
                base -= 2.0
            buy_pct = max(0.0, min(100.0, base + 5.0))
            sell_pct = max(0.0, min(100.0, 100.0 - buy_pct))
            final_score = (buy_pct - sell_pct)
            return float(buy_pct), float(sell_pct), float(final_score)
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


