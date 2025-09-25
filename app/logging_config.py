import logging
import os


def configure_logging(level: str | int = None) -> None:
    """Configure root logging with timestamped format.

    Idempotent: updates existing handlers' formatters if already configured.
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(level)

    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S%z"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    if root.handlers:
        for h in root.handlers:
            # Only update known stream/file handlers
            try:
                h.setFormatter(formatter)
            except Exception:
                # Skip handlers that don't support formatters
                pass
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)

