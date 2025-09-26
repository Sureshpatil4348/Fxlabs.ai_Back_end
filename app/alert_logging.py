import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(payload: Dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # Fallback to simple key=value string when payload is not fully serializable
        pairs = []
        for k, v in payload.items():
            try:
                _ = json.dumps(v, ensure_ascii=False)
                pairs.append(f"{k}={v}")
            except Exception:
                pairs.append(f"{k}={str(v)}")
        return " ".join(pairs)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"event": event, "ts": _now_iso(), **fields}
    # Provide default service/module name if not supplied
    payload.setdefault("service", logger.name)
    logger.log(level, _safe_json(payload))


def log_debug(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.DEBUG, event, **fields)


def log_info(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.INFO, event, **fields)


def log_warning(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.WARNING, event, **fields)


def log_error(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.ERROR, event, **fields)


