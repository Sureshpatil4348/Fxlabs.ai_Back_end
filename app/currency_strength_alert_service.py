import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from .logging_config import configure_logging
from .alert_cache import alert_cache
from .alert_logging import log_debug, log_info, log_warning, log_error
from .concurrency import pair_locks
from .currency_strength import compute_currency_strength_for_timeframe, SUPPORTED_FIAT
from .currency_strength_cache import currency_strength_cache
from .email_service import email_service
from .constants import RSI_SUPPORTED_SYMBOLS
from .mt5_utils import canonicalize_symbol


configure_logging()
logger = logging.getLogger(__name__)


class CurrencyStrengthAlertService:
    """
    Currency Strength alert: triggers whenever the strongest or weakest fiat currency changes.

    - One alert per user (single-alert model) keyed by `currency_strength_tracker` in alert cache.
    - Timeframe: exactly one per alert (>= 5M); we evaluate on closed bars via the minute scheduler.
    - Trigger: fire when either strongest or weakest currency (by normalized strength) changes
      compared to the last observation for this alert.
    - Baseline: on first observation per alert, store current strongest/weakest and skip triggering.
    - Scope: only fiat FX codes in SUPPORTED_FIAT; non-fiat symbols in the pair universe are ignored.
    """

    def __init__(self) -> None:
        # Last winners per alert: {alert_id: {tf: (strongest, weakest, ts_ms)}}
        self._last_winners: Dict[str, Dict[str, Tuple[str, str, int]]] = {}

    def _discover_symbols(self) -> List[str]:
        # Reuse RSI-supported symbols universe; filter happens inside compute helper
        return [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]

    def _tf_map(self):
        from .models import Timeframe as TF
        return {
            "5M": TF.M5,
            "15M": TF.M15,
            "30M": TF.M30,
            "1H": TF.H1,
            "4H": TF.H4,
            "1D": TF.D1,
            "1W": TF.W1,
        }

    def _normalize_timeframe(self, timeframe: str) -> str:
        # Enforce minimum 5M
        return "5M" if (timeframe or "").upper() == "1M" else (timeframe or "1H")

    async def _evaluate_for_alert(self, alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            alert_id = alert.get("id")
            user_email = alert.get("user_email", "")
            alert_name = alert.get("alert_name", "Currency Strength Alert")
            tf_code = self._normalize_timeframe(str(alert.get("timeframe", "1H")).upper())
            tf_map = self._tf_map()
            mt5_tf = tf_map.get(tf_code)
            if not mt5_tf:
                log_warning(logger, "curstr_tf_unsupported", alert_id=alert_id, timeframe=tf_code)
                return None

            # Log concise start/config
            log_debug(
                logger,
                "alert_eval_start",
                alert_type="currency_strength_tracker",
                alert_id=alert_id,
                user_email=user_email,
                timeframe=tf_code,
            )
            log_info(
                logger,
                "alert_eval_config",
                alert_type="currency_strength_tracker",
                alert_id=alert_id,
                user_email=user_email,
                timeframe=tf_code,
            )

            symbols = self._discover_symbols()
            res = await compute_currency_strength_for_timeframe(mt5_tf, symbols)
            if res is None:
                log_warning(logger, "curstr_insufficient_data", alert_id=alert_id, timeframe=tf_code)
                return None
            ts_ms, values = res
            # Update shared cache for potential consumers
            try:
                await currency_strength_cache.update(tf_code, values, ts_ms=ts_ms)
            except Exception:
                pass

            # Determine winners
            strongest = max(values.items(), key=lambda kv: kv[1]) if values else None
            weakest = min(values.items(), key=lambda kv: kv[1]) if values else None
            if not strongest or not weakest:
                return None
            s_cur, s_val = strongest[0], float(strongest[1])
            w_cur, w_val = weakest[0], float(weakest[1])

            per_alert = self._last_winners.setdefault(str(alert_id), {})
            prev = per_alert.get(tf_code)
            # Baseline on first observation
            if prev is None:
                per_alert[tf_code] = (s_cur, w_cur, int(ts_ms or 0))
                log_debug(
                    logger,
                    "curstr_baseline",
                    alert_id=alert_id,
                    timeframe=tf_code,
                    strongest=s_cur,
                    weakest=w_cur,
                    ts_ms=int(ts_ms or 0),
                )
                return None

            s_prev, w_prev, _ = prev
            changed = (s_prev != s_cur) or (w_prev != w_cur)
            if not changed:
                log_debug(
                    logger,
                    "curstr_no_change",
                    alert_id=alert_id,
                    timeframe=tf_code,
                    strongest=s_cur,
                    weakest=w_cur,
                )
                return None

            per_alert[tf_code] = (s_cur, w_cur, int(ts_ms or 0))
            log_info(
                logger,
                "currency_strength_trigger",
                alert_id=alert_id,
                timeframe=tf_code,
                strongest=s_cur,
                weakest=w_cur,
                strongest_value=round(s_val, 2),
                weakest_value=round(w_val, 2),
                prev_strongest=s_prev,
                prev_weakest=w_prev,
            )

            # Build a generic payload with items similar to heatmap entries for hashing/value diffs
            triggered_items = [
                {
                    "symbol": s_cur,
                    "strength": round(s_val, 2),
                    "signal": "strongest",
                    "timeframe": tf_code,
                },
                {
                    "symbol": w_cur,
                    "strength": round(w_val, 2),
                    "signal": "weakest",
                    "timeframe": tf_code,
                },
            ]

            payload = {
                "alert_id": alert_id,
                "alert_name": alert_name,
                "user_email": user_email,
                "timeframe": tf_code,
                "triggered_items": triggered_items,
                "prev": {"strongest": s_prev, "weakest": w_prev},
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "values": values,
            }
            return payload
        except Exception as e:
            log_error(logger, "currency_strength_eval_error", error=str(e))
            return None

    async def check_currency_strength_alerts(self) -> List[Dict[str, Any]]:
        try:
            all_alerts = await alert_cache.get_all_alerts_snapshot()
            triggers: List[Dict[str, Any]] = []

            for _uid, alerts in all_alerts.items():
                for alert in alerts:
                    if alert.get("type") != "currency_strength_tracker" or not alert.get("is_active", True):
                        continue

                    alert_id = alert.get("id")
                    tf_code = self._normalize_timeframe(str(alert.get("timeframe", "1H")).upper())
                    key = f"curstr:{alert_id}:{tf_code}"
                    async with pair_locks.acquire(key):
                        result = await self._evaluate_for_alert(alert)
                        if not result:
                            continue
                        triggers.append(result)
                        methods = alert.get("notification_methods") or ["email"]
                        if "email" in methods:
                            log_info(logger, "email_queue", alert_type="currency_strength_tracker", alert_id=alert_id)
                            asyncio.create_task(
                                email_service.send_currency_strength_alert(
                                    user_email=result.get("user_email", ""),
                                    alert_name=result.get("alert_name", "Currency Strength Alert"),
                                    timeframe=result.get("timeframe", ""),
                                    triggered_items=result.get("triggered_items", []),
                                    prev_winners=result.get("prev", {}),
                                )
                            )
                        else:
                            log_info(
                                logger,
                                "email_disabled",
                                alert_type="currency_strength_tracker",
                                alert_id=alert_id,
                                methods=methods,
                            )

            return triggers
        except Exception as e:
            log_error(logger, "currency_strength_check_error", error=str(e))
            return []


currency_strength_alert_service = CurrencyStrengthAlertService()

