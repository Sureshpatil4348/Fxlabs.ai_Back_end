import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import json

# Optional flag imports (lazy failure-safe) to gate noisy logs
try:
    from .config import ALERT_VERBOSE_LOGS, NEWS_VERBOSE_LOGS  # type: ignore
except Exception:
    ALERT_VERBOSE_LOGS = False  # type: ignore
    NEWS_VERBOSE_LOGS = False  # type: ignore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _level_emoji(level: int) -> str:
    if level >= logging.ERROR:
        return "❌"
    if level >= logging.WARNING:
        return "⚠️"
    if level >= logging.INFO:
        return "ℹ️"
    return "🐞"


def _event_emoji(event: str) -> str:
    mapping = {
        "email_queue": "📤",
        "email_disabled": "📪",
        "rsi_tracker_triggers": "🎯",
        "rsi_alert_triggers": "🎯",
        "heatmap_tracker_trigger": "🔥",
        "indicator_tracker_trigger": "🧭",
        "alert_eval_start": "🧪",
        "alert_eval_end": "🏁",
        "db_trigger_logged": "📝",
        "db_trigger_log_failed": "❌",
        "market_data_loaded": "📦",
        "market_data_stale": "💤",
    }
    return mapping.get(event, "🔔")


def _format_human(level: int, payload: Dict[str, Any]) -> str:
    # human readable single-line: "🔔 event | k1: v1 | k2: v2"
    event = str(payload.get("event", "event"))
    icon = _event_emoji(event)
    # Exclude fields we don't want to duplicate
    exclude_keys = {"event", "ts", "service"}
    # Stable order: sort keys for predictable logs
    parts = []
    for key in sorted(payload.keys()):
        if key in exclude_keys:
            continue
        value = payload[key]
        try:
            if isinstance(value, (dict, list)):
                # Keep it concise; avoid JSON dump per spec
                value_str = "…"  # indicate complex structure omitted
            else:
                value_str = str(value)
        except Exception:
            value_str = "?"
        parts.append(f"{key}: {value_str}")
    kv = " | ".join(parts)
    return f"{icon} {event}{(' | ' + kv) if kv else ''}"


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    # Suppress known-noisy events unless the corresponding verbose flag is enabled
    noisy_alert_events = {
        "alert_eval_start",
        "alert_eval_config",
        "alert_eval_end",
        "closed_bar_unknown",
        "closed_bar_already_evaluated",
        "rsi_no_trigger",
        "market_data_loaded",
        "market_data_stale",
        "heatmap_eval",
        "heatmap_no_trigger",
        "corr_no_mismatch",
        "corr_persisting_mismatch",
        "daily_sleep_until",
        "daily_build_start",
        "daily_build_done",
        "daily_completed",
        "daily_auth_fetch_start",
        "daily_auth_fetch_page",
        "daily_auth_fetch_page_emails",
        "daily_auth_fetch_done",
        "daily_auth_emails",
        "daily_send_batch",
    }
    noisy_news_events = {
        "news_auth_fetch_start",
        "news_auth_fetch_page",
        "news_auth_fetch_page_emails",
        "news_auth_fetch_done",
        "news_users_fetch_fallback_alert_tables",
        "news_reminder_due_items",
        "news_auth_emails",
        "news_reminder_recipients",
        "news_reminder_completed",
    }
    if (event in noisy_alert_events and not ALERT_VERBOSE_LOGS) or (
        event in noisy_news_events and not NEWS_VERBOSE_LOGS
    ):
        return

    payload: Dict[str, Any] = {"event": event, "ts": _now_iso(), **fields}
    # Provide default service/module name if not supplied
    payload.setdefault("service", logger.name)
    logger.log(level, _format_human(level, payload))


def log_debug(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.DEBUG, event, **fields)


def log_info(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.INFO, event, **fields)


def log_warning(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.WARNING, event, **fields)


def log_error(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.ERROR, event, **fields)

