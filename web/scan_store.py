"""
SQLite-backed scan state store for the web interface.

Solves the problem of in-memory scan state being lost when the gunicorn
worker is killed/restarted (OOM, process recycling on Render free plan, etc.).

Scan state is persisted to SQLite so:
- Status polls survive worker restarts
- Stale "running" scans are detected and marked as timed-out
- Old completed scans are cleaned up automatically
"""

from __future__ import annotations

import html as html_mod
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger("spotify_audit.web.scan_store")

# Default location alongside other data files
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "spotify_audit" / "data" / "web_scans.db"

# Limits
SCAN_TIMEOUT_SECONDS = 300       # 5 minutes max for a single scan
STALE_HEARTBEAT_SECONDS = 45     # If no heartbeat for 45s, scan thread is dead
CLEANUP_AGE_HOURS = 24           # Remove completed scans older than this


def _timeout_report_html(title: str, message: str, elapsed_s: int) -> str:
    """Generate a minimal HTML report page for timed-out / interrupted scans."""
    safe_title = html_mod.escape(title)
    safe_msg = html_mod.escape(message)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  body {{ background:#06090f; color:#c8d0da; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:24px 16px; line-height:1.5 }}
  .container {{ max-width:800px; margin:0 auto }}
  h1 {{ color:#f59e0b; margin-bottom:8px }}
  .warn-box {{ background:#1a1510; border:1px solid #3d2e1a; border-radius:10px; padding:20px; margin:16px 0 }}
  .retry {{ display:inline-block; margin-top:16px; padding:10px 24px; background:#1DB954; color:#000; border-radius:8px; text-decoration:none; font-weight:700 }}
  .retry:hover {{ background:#1ed760 }}
</style></head><body>
<div class="container">
  <h1>{safe_title}</h1>
  <div class="warn-box">
    <p style="color:#f59e0b;font-weight:600;margin-bottom:8px">{safe_msg}</p>
    <p style="color:#94a3b8;font-size:0.85rem">
      Elapsed time: {elapsed_s}s. This usually happens when the playlist has many
      artists and the external API lookups take too long. You can try again &mdash;
      some results may have been cached for next time.
    </p>
  </div>
  <a class="retry" href="/">Try Again</a>
</div></body></html>"""


class ScanStore:
    """Thread-safe SQLite-backed store for web scan state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB)
        self._lock = threading.Lock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._recover_stale_scans()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS web_scans (
                    scan_id         TEXT PRIMARY KEY,
                    status          TEXT NOT NULL DEFAULT 'running',
                    phase           TEXT DEFAULT 'starting',
                    current         INTEGER DEFAULT 0,
                    total           INTEGER DEFAULT 0,
                    message         TEXT DEFAULT '',
                    result_html     TEXT,
                    error           TEXT,
                    started_at      REAL NOT NULL,
                    last_heartbeat  REAL NOT NULL,
                    playlist_name   TEXT
                )
            """)

    def _recover_stale_scans(self) -> None:
        """On startup, mark any still-running scans as complete with an error report."""
        msg = "The server restarted while this scan was running. Please try again."
        html = _timeout_report_html("Scan Interrupted", msg, 0)
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE web_scans SET status='complete', phase='done', "
                "result_html=?, message=? "
                "WHERE status='running'",
                (html, msg),
            )
            if cursor.rowcount > 0:
                logger.info("Recovered %d stale scans on startup", cursor.rowcount)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, scan_id: str) -> None:
        """Register a new scan."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_scans "
                "(scan_id, status, phase, current, total, message, "
                " result_html, error, started_at, last_heartbeat, playlist_name) "
                "VALUES (?, 'running', 'starting', 0, 0, 'Starting scan...', "
                "        NULL, NULL, ?, ?, NULL)",
                (scan_id, now, now),
            )

    def heartbeat(self, scan_id: str, **fields: object) -> None:
        """Update progress fields and refresh heartbeat timestamp.

        Only writes to SQLite — called periodically from the scan thread
        to avoid hammering the DB on every progress tick.
        """
        fields["last_heartbeat"] = time.time()
        set_parts = [f"{k}=?" for k in fields]
        values = list(fields.values()) + [scan_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE web_scans SET {', '.join(set_parts)} WHERE scan_id=?",
                values,
            )

    def mark_complete(self, scan_id: str, result_html: str,
                      playlist_name: str | None, message: str) -> None:
        """Mark a scan as successfully completed."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE web_scans SET status='complete', phase='done', "
                "result_html=?, playlist_name=?, message=?, "
                "last_heartbeat=? WHERE scan_id=?",
                (result_html, playlist_name, message, time.time(), scan_id),
            )

    def mark_error(self, scan_id: str, error: str, message: str) -> None:
        """Mark a scan as failed."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE web_scans SET status='error', phase='error', "
                "error=?, message=?, last_heartbeat=? WHERE scan_id=?",
                (error, message, time.time(), scan_id),
            )

    def get(self, scan_id: str) -> dict | None:
        """Retrieve scan state, with automatic stale/timeout detection."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM web_scans WHERE scan_id=?", (scan_id,)
            ).fetchone()

        if not row:
            return None

        result = dict(row)

        # Check for stale/timed-out running scans.
        # When detected, generate an error report and mark as "complete"
        # so the user always has a report page to view.
        if result["status"] == "running":
            now = time.time()
            elapsed = now - result["started_at"]
            heartbeat_age = now - result["last_heartbeat"]

            if elapsed > SCAN_TIMEOUT_SECONDS:
                error_msg = (
                    f"Scan timed out after {int(elapsed)}s. "
                    "The playlist may have too many artists for the server. "
                    "Try a smaller playlist or use the CLI tool."
                )
                html = _timeout_report_html("Scan Timed Out", error_msg, int(elapsed))
                self.mark_complete(scan_id, result_html=html,
                                   playlist_name=result.get("playlist_name"),
                                   message=error_msg)
                result["status"] = "complete"
                result["phase"] = "done"
                result["result_html"] = html
                result["message"] = error_msg
            elif heartbeat_age > STALE_HEARTBEAT_SECONDS:
                error_msg = (
                    "Scan was interrupted unexpectedly. "
                    "The server may have run out of memory or the scan thread "
                    "stopped responding. Please try again."
                )
                html = _timeout_report_html("Scan Interrupted", error_msg, int(elapsed))
                self.mark_complete(scan_id, result_html=html,
                                   playlist_name=result.get("playlist_name"),
                                   message=error_msg)
                result["status"] = "complete"
                result["phase"] = "done"
                result["result_html"] = html
                result["message"] = error_msg

        return result

    def get_result_html(self, scan_id: str) -> str | None:
        """Retrieve the HTML report for a completed scan."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT result_html FROM web_scans WHERE scan_id=? AND status='complete'",
                (scan_id,),
            ).fetchone()
        return row["result_html"] if row else None

    def count_active(self) -> int:
        """Count currently running scans."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM web_scans WHERE status='running'"
            ).fetchone()
        return row["n"]

    def cleanup_old(self) -> int:
        """Remove old completed/errored scans."""
        cutoff = time.time() - (CLEANUP_AGE_HOURS * 3600)
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM web_scans WHERE started_at < ? AND status != 'running'",
                (cutoff,),
            )
            return cursor.rowcount
