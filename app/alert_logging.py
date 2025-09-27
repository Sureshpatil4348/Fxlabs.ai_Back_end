import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional


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


