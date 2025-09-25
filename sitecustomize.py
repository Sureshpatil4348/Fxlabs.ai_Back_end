"""
Global console print timestamping.

This module is auto-imported by Python's 'site' during interpreter startup
if the project root is on sys.path (which is the default when running from
this directory). It monkey-patches builtins.print to prefix all console
output with an ISO-8601 timestamp.
"""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any, Callable

_ORIGINAL_PRINT: Callable[..., None] = builtins.print


def _now_ts() -> str:
    # Local time with timezone, second precision (e.g., 2025-09-25T12:34:56+05:30)
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _enable_timestamped_print() -> None:
    # Idempotent: avoid double-wrapping if already enabled
    if getattr(builtins.print, "_is_timestamped", False):  # type: ignore[attr-defined]
        return

    def ts_print(*args: Any, **kwargs: Any) -> None:
        _ORIGINAL_PRINT(f"[{_now_ts()}]", *args, **kwargs)

    # Mark wrapper to prevent re-wrapping
    setattr(ts_print, "_is_timestamped", True)  # type: ignore[attr-defined]
    builtins.print = ts_print  # type: ignore[assignment]


# Enable on import so all subsequent prints are timestamped
_enable_timestamped_print()

