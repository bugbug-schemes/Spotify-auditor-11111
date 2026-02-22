"""
Spotify Audit — Web Interface

A Flask app that lets users paste a Spotify playlist URL
and get an HTML report back, with real-time progress updates.

Usage:
    python web/app.py                  # dev server on port 5000
    python web/app.py --port 8080      # custom port
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, render_template, send_from_directory

from spotify_audit.audit_runner import run_audit, build_config
from spotify_audit.reports.formatter import to_html
from web.api import cms_api, init_db

app = Flask(__name__, template_folder="templates", static_folder="static")
app.register_blueprint(cms_api)
logger = logging.getLogger("spotify_audit.web")

# ---------------------------------------------------------------------------
# Persistent scan store — SQLite-backed so reports survive server restarts
# ---------------------------------------------------------------------------
SCAN_DB_PATH = Path(__file__).parent.parent / "spotify_audit" / "data" / "scan_reports.db"


class ScanStore:
    """SQLite store for completed scan reports."""

    def __init__(self, db_path: Path = SCAN_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_schema(self._get_conn())

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_reports (
                scan_id       TEXT PRIMARY KEY,
                status        TEXT NOT NULL,
                result_html   TEXT,
                playlist_name TEXT,
                message       TEXT,
                error         TEXT,
                created_at    REAL NOT NULL
            )
        """)
        conn.commit()

    def save(self, scan_id: str, scan: dict) -> None:
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO scan_reports
                (scan_id, status, result_html, playlist_name, message, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_id,
            scan["status"],
            scan.get("result_html"),
            scan.get("playlist_name"),
            scan.get("message"),
            scan.get("error"),
            scan.get("started_at", time.time()),
        ))
        conn.commit()

    def get(self, scan_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM scan_reports WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def recover_interrupted(self) -> int:
        """Mark any scans left in 'running' state as interrupted (server died mid-scan)."""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE scan_reports
               SET status = 'error',
                   error  = 'Scan interrupted by server restart. Please try again.',
                   message = 'Scan interrupted by server restart. Please try again.'
             WHERE status = 'running'
        """)
        conn.commit()
        return cursor.rowcount


scan_store = ScanStore()
# On startup, mark any orphaned running scans as interrupted
_recovered = scan_store.recover_interrupted()
if _recovered:
    logger.info("Recovered %d interrupted scan(s) from previous run", _recovered)

# Serve the Entity Review CMS React app at /cms
CMS_DIR = Path(__file__).parent / "static" / "cms"


@app.route("/cms")
@app.route("/cms/<path:subpath>")
def serve_cms(subpath=""):
    """Serve the React CMS SPA. All routes fall through to index.html."""
    # Try to serve a real file first (JS, CSS, assets)
    if subpath:
        file_path = CMS_DIR / subpath
        if file_path.is_file():
            return send_from_directory(CMS_DIR, subpath)
    # Otherwise serve index.html for client-side routing
    return send_from_directory(CMS_DIR, "index.html")

# Bounded in-memory scan store — evicts oldest entries when full
MAX_SCANS = 100
MAX_CONCURRENT_SCANS = 5

_scans_lock = threading.Lock()
scans: OrderedDict[str, dict] = OrderedDict()
_active_scan_count = 0


def _evict_old_scans() -> None:
    """Remove oldest completed scans when store exceeds MAX_SCANS."""
    while len(scans) > MAX_SCANS:
        # Remove the oldest entry
        scans.popitem(last=False)


def _run_scan_background(scan_id: str, playlist_url: str, deep: bool) -> None:
    """Background thread: run the audit and store results."""
    global _active_scan_count

    def on_progress(phase: str, current: int, total: int, message: str):
        scans[scan_id].update({
            "phase": phase,
            "current": current,
            "total": total,
            "message": message,
        })

    try:
        config = build_config()
        playlist_report, blocklist_report = run_audit(
            playlist_url=playlist_url,
            deep=deep,
            config=config,
            on_progress=on_progress,
            use_cache=False,
            use_entity_db=False,
        )
        html = to_html(playlist_report)
        scans[scan_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "playlist_name": playlist_report.playlist_name,
            "message": f"Done! Analyzed {playlist_report.total_unique_artists} artists.",
        })
        # Persist completed scan to SQLite so it survives server restarts
        scan_store.save(scan_id, scans[scan_id])
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Scan %s failed", scan_id)
        scans[scan_id].update({
            "status": "error",
            "phase": "error",
            "error": str(exc),
            "message": f"Error: {exc}\n\nTraceback:\n{tb}",
        })
        scan_store.save(scan_id, scans[scan_id])
    finally:
        with _scans_lock:
            _active_scan_count -= 1


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    global _active_scan_count

    data = request.get_json(silent=True) or {}
    playlist_url = data.get("url", "").strip()
    deep = bool(data.get("deep", False))

    if not playlist_url:
        return jsonify({"error": "Please provide a playlist URL"}), 400

    # Accept various Spotify URL formats
    if "spotify.com" not in playlist_url and "spotify:" not in playlist_url:
        return jsonify({"error": "Please provide a valid Spotify playlist URL"}), 400

    # Check concurrent scan limit
    with _scans_lock:
        if _active_scan_count >= MAX_CONCURRENT_SCANS:
            return jsonify({
                "error": f"Server busy — {_active_scan_count} scans running. Please try again shortly."
            }), 503
        _active_scan_count += 1

    scan_id = uuid.uuid4().hex[:8]
    scans[scan_id] = {
        "status": "running",
        "phase": "starting",
        "current": 0,
        "total": 0,
        "message": "Starting scan...",
        "result_html": None,
        "error": None,
        "started_at": time.time(),
        "playlist_name": None,
    }
    # Persist immediately so we can detect interrupted scans after a restart
    scan_store.save(scan_id, scans[scan_id])

    # Evict old completed scans if store is too large
    with _scans_lock:
        _evict_old_scans()

    thread = threading.Thread(
        target=_run_scan_background,
        args=(scan_id, playlist_url, deep),
        daemon=True,
    )
    thread.start()

    return jsonify({"scan_id": scan_id})


@app.route("/api/scan/<scan_id>")
def scan_status(scan_id):
    # Check in-memory store first (for running scans with live progress)
    scan = scans.get(scan_id)
    if scan:
        resp = {k: v for k, v in scan.items() if k != "result_html"}
        resp["has_result"] = scan["result_html"] is not None
        elapsed = time.time() - scan["started_at"]
        resp["elapsed_seconds"] = round(elapsed, 1)
        return jsonify(resp)

    # Fall back to SQLite for completed scans that survived a restart
    saved = scan_store.get(scan_id)
    if saved:
        return jsonify({
            "status": saved["status"],
            "phase": "done" if saved["status"] == "complete" else "error",
            "has_result": saved["result_html"] is not None,
            "playlist_name": saved["playlist_name"],
            "message": saved["message"],
            "error": saved.get("error"),
            "elapsed_seconds": 0,
            "current": 0,
            "total": 0,
        })

    return jsonify({"error": "Scan not found"}), 404


@app.route("/report/<scan_id>")
def view_report(scan_id):
    # Check in-memory first
    scan = scans.get(scan_id)
    if scan and scan["status"] == "complete" and scan["result_html"]:
        return scan["result_html"]

    # Fall back to SQLite
    saved = scan_store.get(scan_id)
    if saved:
        if saved["status"] == "complete" and saved["result_html"]:
            return saved["result_html"]
        if saved["status"] == "error":
            return saved.get("error") or "Scan failed", 410

    return "Report not ready yet", 404


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spotify Audit Web Server")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--db", default=None,
                        help="Path to entities.db (default: spotify_audit/data/entities.db)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Initialize the entity database for CMS routes
    init_db(args.db)

    print(f"\n  Spotify Audit Web — http://{args.host}:{args.port}")
    print(f"  CMS API available at /api/cms/*\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
