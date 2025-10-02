import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


_SERVER_START_ISO_NAME = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def configure_logging(level: str | int = None) -> None:
    """Configure root logging with timestamped format and file output.

    - Always ensures a console stream handler is present.
    - Also writes logs to `logs/<YYYY-MM-DDTHH-mm-ssZ>.log` (UTC, per server start), rotating at ~10MB x5.
    - Idempotent: updates existing handlers' formatters if already configured,
      and adds the file handler only once.
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(level)

    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S%z"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Determine log directory in repo root: <repo>/logs
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    except Exception:
        base_dir = os.getcwd()

    log_dir = os.environ.get("LOG_DIR", os.path.join(base_dir, "logs"))
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        # Fallback to current working directory if creation fails
        log_dir = os.getcwd()

    # Enforce per-start log file naming: <UTC server start datetime>.log
    # Example: logs/2025-09-30T14-05-33Z.log
    log_file_name = f"{_SERVER_START_ISO_NAME}.log"
    log_file_path = os.path.join(log_dir, log_file_name)

    # Apply formatter to existing handlers and detect if our file handler exists
    has_console = False
    has_target_file = False
    for h in list(root.handlers):
        try:
            h.setFormatter(formatter)
        except Exception:
            pass
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            has_console = True
        if isinstance(h, logging.FileHandler):
            try:
                if os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(log_file_path):
                    has_target_file = True
            except Exception:
                pass

    # Ensure console stream handler exists
    if not has_console:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Ensure rotating file handler exists (10MB x 5 backups)
    if not has_target_file:
        max_bytes = int(os.environ.get("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
        backup_count = int(os.environ.get("LOG_BACKUP_COUNT", "5"))
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Suppress noisy third-party debug logs (e.g., SendGrid client payloads)
    noisy_loggers = [
        "python_http_client",
        "python_http_client.client",
        "sendgrid",
        "urllib3",
        "requests",
    ]
    for name in noisy_loggers:
        try:
            logging.getLogger(name).setLevel(logging.WARNING)
        except Exception:
            pass

    # Dump current environment for operator visibility (including secrets per request)
    try:
        env_snapshot = {k: v for k, v in os.environ.items()}
        root.info("🌐 ENV DUMP START")
        for key in sorted(env_snapshot):
            root.info("ENV %s=%s", key, env_snapshot[key])
        root.info("🌐 ENV DUMP END")
    except Exception as exc:
        root.warning("Failed to dump environment variables: %s", exc)
