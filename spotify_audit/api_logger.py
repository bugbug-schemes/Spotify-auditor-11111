"""
Lightweight API call logger.

Wraps around EntityDB.log_api_call() to provide a simple interface
for logging external API calls from any client module.

Usage:
    from spotify_audit.api_logger import log_call

    # After an API call:
    log_call("deezer", "/search/artist", artist_name="Luna",
             status_code=200, response_time_ms=142)

    # On error:
    log_call("genius", "/search", artist_name="Luna",
             status_code=429, error_message="Rate limited")

The logger is optional — if no EntityDB is configured, calls are silently dropped.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Module-level reference to the shared EntityDB (set during app init)
_entity_db: Any = None


def configure(entity_db: Any) -> None:
    """Set the EntityDB instance for API logging."""
    global _entity_db
    _entity_db = entity_db


def log_call(
    api_name: str,
    endpoint: str = "",
    artist_name: str = "",
    status_code: int | None = None,
    response_time_ms: int | None = None,
    error_message: str = "",
) -> None:
    """Log an API call. No-op if not configured."""
    if _entity_db is None:
        return
    try:
        _entity_db.log_api_call(
            api_name=api_name,
            endpoint=endpoint,
            artist_name=artist_name,
            status_code=status_code,
            response_time_ms=response_time_ms,
            error_message=error_message,
        )
    except Exception:
        pass  # Never let logging break the pipeline


@contextmanager
def timed_call(api_name: str, endpoint: str = "", artist_name: str = ""):
    """Context manager that auto-logs timing and status.

    Usage:
        with timed_call("deezer", "/search/artist", "Luna") as ctx:
            response = requests.get(...)
            ctx["status_code"] = response.status_code
            if not response.ok:
                ctx["error_message"] = response.text[:200]
    """
    ctx: dict[str, Any] = {"status_code": None, "error_message": ""}
    start = time.monotonic()
    try:
        yield ctx
    except Exception as exc:
        ctx["error_message"] = str(exc)[:200]
        raise
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log_call(
            api_name=api_name,
            endpoint=endpoint,
            artist_name=artist_name,
            status_code=ctx.get("status_code"),
            response_time_ms=elapsed_ms,
            error_message=ctx.get("error_message", ""),
        )
