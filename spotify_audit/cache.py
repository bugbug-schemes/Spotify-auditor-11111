"""
SQLite cache with configurable TTL for artist analysis results.

Stores serialized JSON keyed by (artist_id, tier) with automatic expiry.
Includes an in-memory layer to avoid repeated SQLite reads within a session.

Thread-safe: uses per-thread SQLite connections and a lock for shared memory.
"""

from __future__ import annotations

import json
import sqlite3
import threading
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

# Sentinel to distinguish "not in memory cache" from "cached as None"
_MISS = object()


class Cache:
    """Key-value cache backed by SQLite with an in-memory read-through layer.

    Thread-safe: each thread gets its own SQLite connection via threading.local().
    The in-memory cache is protected by a lock.
    """

    def __init__(self, db_path: Path, ttl_days: int = 7) -> None:
        self.ttl_seconds = ttl_days * 86400
        self._db_path = str(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._mem_lock = threading.Lock()
        # In-memory cache: {key: (parsed_value | None, created_at)}
        self._mem: dict[str, tuple[dict[str, Any] | None, float]] = {}
        # Initialize schema on the creating thread's connection
        conn = self._get_conn()
        conn.execute(CREATE_TABLE)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn = conn
        return conn

    # -- public API ---------------------------------------------------------

    def get(self, artist_id: str, tier: str) -> dict[str, Any] | None:
        """Return cached result or None if missing / expired."""
        key = self._key(artist_id, tier)
        now = time.time()

        # Check memory first
        with self._mem_lock:
            mem_entry = self._mem.get(key, _MISS)
        if mem_entry is not _MISS:
            value, created_at = mem_entry
            if value is None or now - created_at > self.ttl_seconds:
                return None
            return value

        # Fall through to SQLite (thread-local connection)
        conn = self._get_conn()
        row = conn.execute(SELECT, (key,)).fetchone()
        if row is None:
            with self._mem_lock:
                self._mem[key] = (None, 0.0)  # Cache the miss
            return None
        value_json, created_at = row
        if now - created_at > self.ttl_seconds:
            logger.debug("Cache expired for %s", key)
            with self._mem_lock:
                self._mem[key] = (None, 0.0)
            return None
        parsed = json.loads(value_json)
        with self._mem_lock:
            self._mem[key] = (parsed, created_at)
        return parsed

    def put(self, artist_id: str, tier: str, value: dict[str, Any]) -> None:
        """Insert or update a cache entry."""
        key = self._key(artist_id, tier)
        now = time.time()
        conn = self._get_conn()
        conn.execute(UPSERT, (key, json.dumps(value), now))
        conn.commit()
        with self._mem_lock:
            self._mem[key] = (value, now)

    def put_deferred(self, artist_id: str, tier: str, value: dict[str, Any]) -> None:
        """Insert/update without committing — call flush() when the batch is done."""
        key = self._key(artist_id, tier)
        now = time.time()
        conn = self._get_conn()
        conn.execute(UPSERT, (key, json.dumps(value), now))
        with self._mem_lock:
            self._mem[key] = (value, now)

    def flush(self) -> None:
        """Commit any pending deferred writes."""
        self._get_conn().commit()

    def purge_expired(self) -> int:
        """Remove all entries older than TTL. Returns count deleted."""
        cutoff = time.time() - self.ttl_seconds
        conn = self._get_conn()
        cur = conn.execute(DELETE_EXPIRED, (cutoff,))
        conn.commit()
        with self._mem_lock:
            self._mem = {
                k: v for k, v in self._mem.items()
                if v[1] >= cutoff
            }
        return cur.rowcount

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        with self._mem_lock:
            self._mem.clear()

    # -- internal -----------------------------------------------------------

    @staticmethod
    def _key(artist_id: str, tier: str) -> str:
        return f"{artist_id}:{tier}"
