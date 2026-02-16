"""
SQLite cache with configurable TTL for artist analysis results.

Stores serialized JSON keyed by (artist_id, tier) with automatic expiry.
"""

from __future__ import annotations

import json
import sqlite3
import time
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""

UPSERT = """
INSERT INTO cache (key, value, created_at)
VALUES (?, ?, ?)
ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at;
"""

SELECT = "SELECT value, created_at FROM cache WHERE key = ?;"

DELETE_EXPIRED = "DELETE FROM cache WHERE created_at < ?;"


class Cache:
    """Simple key-value cache backed by SQLite."""

    def __init__(self, db_path: Path, ttl_days: int = 7) -> None:
        self.ttl_seconds = ttl_days * 86400
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(CREATE_TABLE)
        self.conn.commit()

    # -- public API ---------------------------------------------------------

    def get(self, artist_id: str, tier: str) -> dict[str, Any] | None:
        """Return cached result or None if missing / expired."""
        key = self._key(artist_id, tier)
        row = self.conn.execute(SELECT, (key,)).fetchone()
        if row is None:
            return None
        value_json, created_at = row
        if time.time() - created_at > self.ttl_seconds:
            logger.debug("Cache expired for %s", key)
            return None
        return json.loads(value_json)

    def put(self, artist_id: str, tier: str, value: dict[str, Any]) -> None:
        """Insert or update a cache entry."""
        key = self._key(artist_id, tier)
        self.conn.execute(UPSERT, (key, json.dumps(value), time.time()))
        self.conn.commit()

    def put_deferred(self, artist_id: str, tier: str, value: dict[str, Any]) -> None:
        """Insert/update without committing — call flush() when the batch is done."""
        key = self._key(artist_id, tier)
        self.conn.execute(UPSERT, (key, json.dumps(value), time.time()))

    def flush(self) -> None:
        """Commit any pending deferred writes."""
        self.conn.commit()

    def purge_expired(self) -> int:
        """Remove all entries older than TTL. Returns count deleted."""
        cutoff = time.time() - self.ttl_seconds
        cur = self.conn.execute(DELETE_EXPIRED, (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()

    # -- internal -----------------------------------------------------------

    @staticmethod
    def _key(artist_id: str, tier: str) -> str:
        return f"{artist_id}:{tier}"
