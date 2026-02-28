"""
Spotify Audit — Web Interface

A Flask app that lets users paste a Spotify playlist URL
and get an HTML report back, with real-time progress updates.

Scan state is persisted to SQLite so that completed reports survive
gunicorn worker restarts, OOM kills, and other transient failures.

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
from flask_limiter import Limiter

from web.api import cms_api, init_db

app = Flask(__name__, template_folder="templates", static_folder="static")
app.register_blueprint(cms_api)
logger = logging.getLogger("spotify_audit.web")


def _get_real_ip():
    """Get real client IP, even behind reverse proxy (Render, Railway, etc.)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


limiter = Limiter(
    key_func=_get_real_ip,
    app=app,
    default_limits=[],        # No global limit — only on specific endpoints
    storage_uri="memory://",  # In-memory store; swap to redis:// for multi-process
)

# ---------------------------------------------------------------------------
# Demo mode — serve cached report for UI development
# ---------------------------------------------------------------------------
DEMO_MODE_ENABLED = os.environ.get("DEMO_MODE_ENABLED", "true").lower() in ("true", "1", "yes")
DEMO_CACHE_PATH = PROJECT_ROOT / "data" / "demo" / "cached_report.html"

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
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
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
        # Add columns for retry support (idempotent)
        for col, coltype in [
            ("playlist_url", "TEXT"),
            ("skipped_json", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE scan_reports ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()

    def save(self, scan_id: str, scan: dict) -> None:
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO scan_reports
                (scan_id, status, result_html, playlist_name, message, error, created_at,
                 playlist_url, skipped_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_id,
            scan["status"],
            scan.get("result_html"),
            scan.get("playlist_name"),
            scan.get("message"),
            scan.get("error"),
            scan.get("started_at", time.time()),
            scan.get("playlist_url"),
            scan.get("skipped_json"),
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
_active_scans: OrderedDict[str, dict] = OrderedDict()
_active_scan_count = 0


def _evict_old_scans() -> None:
    """Remove oldest completed scans when store exceeds MAX_SCANS."""
    while len(_active_scans) > MAX_SCANS:
        _active_scans.popitem(last=False)


def _build_error_report(playlist_url: str, exc: Exception, tb: str) -> str:
    """Generate a minimal HTML error report so the user always sees something."""
    import html as html_mod
    safe_url = html_mod.escape(str(playlist_url))
    safe_err = html_mod.escape(str(exc))
    safe_tb = html_mod.escape(tb)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scan Error Report</title>
<style>
  body {{ background:#06090f; color:#c8d0da; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:24px 16px; line-height:1.5 }}
  .container {{ max-width:800px; margin:0 auto }}
  h1 {{ color:#ef4444; margin-bottom:8px }}
  .error-box {{ background:#1a1010; border:1px solid #5a2020; border-radius:10px; padding:20px; margin:16px 0 }}
  .url {{ color:#94a3b8; font-size:0.9rem; word-break:break-all }}
  pre {{ background:#0d1219; border:1px solid #1a2332; border-radius:8px; padding:16px; overflow-x:auto; font-size:0.8rem; color:#667788 }}
  .retry {{ display:inline-block; margin-top:16px; padding:10px 24px; background:#1DB954; color:#000; border-radius:8px; text-decoration:none; font-weight:700 }}
  .retry:hover {{ background:#1ed760 }}
</style></head><body>
<div class="container">
  <h1>Scan Failed</h1>
  <p class="url">Playlist: {safe_url}</p>
  <div class="error-box">
    <p style="color:#ff6b6b;font-weight:600;margin-bottom:8px">Error: {safe_err}</p>
    <p style="color:#94a3b8;font-size:0.85rem">
      The scan could not complete. This is usually caused by the playlist being
      too large for the server, a temporary API outage, or the Spotify endpoint
      being unavailable. Try a smaller playlist or try again later.
    </p>
  </div>
  <details style="margin-top:16px">
    <summary style="cursor:pointer;color:#667788;font-size:0.85rem">Technical Details</summary>
    <pre>{safe_tb}</pre>
  </details>
  <a class="retry" href="/">Try Another Scan</a>
</div></body></html>"""


def _run_scan_background(scan_id: str, playlist_url: str, deep: bool) -> None:
    """Background thread: run the audit and store results."""
    global _active_scan_count

    # Lazy-import heavy scan machinery so the Flask app starts instantly.
    # These imports pull in 13+ API clients and ML modules — too slow for
    # worker init on Render's free tier where CPU is limited.
    from spotify_audit.audit_runner import run_audit, build_config
    from spotify_audit.reports.formatter import to_html

    def on_progress(phase: str, current: int, total: int, message: str):
        if scan_id in _active_scans:
            _active_scans[scan_id].update({
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

        skipped_count = len(playlist_report.skipped_artists)
        analyzed = playlist_report.total_unique_artists - skipped_count
        if skipped_count:
            done_msg = (
                f"Done! Analyzed {analyzed} artists "
                f"({skipped_count} skipped due to errors/timeouts)."
            )
        else:
            done_msg = f"Done! Analyzed {analyzed} artists."

        import json as _json
        skipped_json = _json.dumps(playlist_report.skipped_artists) if playlist_report.skipped_artists else None

        _active_scans[scan_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "playlist_name": playlist_report.playlist_name,
            "message": done_msg,
            "playlist_url": playlist_url,
            "skipped_json": skipped_json,
        })
        # Persist completed scan to SQLite so it survives server restarts
        scan_store.save(scan_id, _active_scans[scan_id])
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Scan %s failed", scan_id)

        # Even on total failure, generate a minimal error report
        html = _build_error_report(playlist_url, exc, tb)
        _active_scans[scan_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "message": f"Scan failed: {exc}",
        })
        scan_store.save(scan_id, _active_scans[scan_id])
    finally:
        with _scans_lock:
            _active_scan_count -= 1


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("index.html"), 404


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error": "Rate limit exceeded",
        "message": "Maximum 5 scans per minute. Please wait before trying again.",
        "retry_after": str(e.description),
    }), 429


@app.errorhandler(500)
def internal_error(e):
    logger.exception("Internal server error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return "Internal server error", 500


@app.route("/healthz")
def healthz():
    """Lightweight health check for Render — no heavy imports needed."""
    return jsonify({"status": "ok"}), 200


@app.route("/")
def index():
    return render_template("index.html", demo_enabled=DEMO_MODE_ENABLED)


@app.route("/api/scan", methods=["POST"])
@limiter.limit("5/minute")
def start_scan():
    global _active_scan_count

    data = request.get_json(silent=True) or {}
    playlist_url = data.get("url", "").strip()
    deep = bool(data.get("deep", False))

    if not playlist_url:
        return jsonify({"error": "Please provide a playlist URL"}), 400

    # Validate Spotify URL format strictly to prevent SSRF
    import re
    _SPOTIFY_URL_RE = re.compile(
        r'^https?://open\.spotify\.com/(playlist|album|track|artist)/[a-zA-Z0-9]+',
    )
    if not _SPOTIFY_URL_RE.match(playlist_url) and not playlist_url.startswith("spotify:"):
        return jsonify({"error": "Please provide a valid Spotify playlist URL"}), 400

    # Check concurrent scan limit
    with _scans_lock:
        if _active_scan_count >= MAX_CONCURRENT_SCANS:
            return jsonify({
                "error": f"Server busy — {_active_scan_count} scans running. Please try again shortly."
            }), 503
        _active_scan_count += 1

    scan_id = uuid.uuid4().hex[:16]
    scan_entry = {
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
    with _scans_lock:
        _active_scans[scan_id] = scan_entry
    # Persist immediately so we can detect interrupted scans after a restart
    scan_store.save(scan_id, scan_entry)

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
    scan = _active_scans.get(scan_id)
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
    scan = _active_scans.get(scan_id)
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


@app.route("/demo")
def demo_report():
    """Serve a cached report for UI development — no pipeline, no API calls."""
    if not DEMO_MODE_ENABLED:
        return "Not found", 404

    if not DEMO_CACHE_PATH.is_file():
        return "Demo report not available. Run: python scripts/generate_demo_cache.py", 404

    html = DEMO_CACHE_PATH.read_text(encoding="utf-8")

    # Inject dismissible demo banner right after <body>
    demo_banner = """<div id="demoBanner" style="
      background:#1a2332; border-bottom:1px solid #2a3a4a;
      padding:8px 16px; text-align:center; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      font-size:0.85rem; color:#94a3b8; position:relative; z-index:9999;
    ">
      <span style="margin-right:8px">&#9889;</span>
      <strong style="color:#c8d0da">Demo Mode</strong>
      &mdash; Viewing cached report data. Results may not reflect the latest analysis.
      <button onclick="document.getElementById('demoBanner').remove()" style="
        background:none; border:none; color:#667788; cursor:pointer;
        font-size:1.1rem; margin-left:12px; padding:0 4px; vertical-align:middle;
      " aria-label="Dismiss">&times;</button>
    </div>"""

    html = html.replace("<body>", "<body>" + demo_banner, 1)
    return html


@app.route("/api/scan/<scan_id>/retry-skipped", methods=["POST"])
def retry_skipped(scan_id):
    """Retry analysis for artists that timed out in a previous scan."""
    global _active_scan_count
    import json as _json

    # Find skipped data from in-memory store or SQLite
    scan = _active_scans.get(scan_id)
    skipped_json = None
    if scan:
        skipped_json = scan.get("skipped_json")
    if not skipped_json:
        saved = scan_store.get(scan_id)
        if saved:
            skipped_json = saved.get("skipped_json")

    if not skipped_json:
        return jsonify({"error": "No skipped artists found for this scan"}), 404

    try:
        skipped = _json.loads(skipped_json)
    except (TypeError, _json.JSONDecodeError):
        return jsonify({"error": "Invalid skipped artists data"}), 500

    if not skipped:
        return jsonify({"error": "No skipped artists to retry"}), 400

    # Check concurrent scan limit
    with _scans_lock:
        if _active_scan_count >= MAX_CONCURRENT_SCANS:
            return jsonify({
                "error": f"Server busy — {_active_scan_count} scans running. Please try again shortly."
            }), 503
        _active_scan_count += 1

    retry_id = uuid.uuid4().hex[:8]
    _active_scans[retry_id] = {
        "status": "running",
        "phase": "starting",
        "current": 0,
        "total": len(skipped),
        "message": f"Retrying {len(skipped)} skipped artists...",
        "result_html": None,
        "error": None,
        "started_at": time.time(),
        "playlist_name": None,
    }
    scan_store.save(retry_id, _active_scans[retry_id])

    thread = threading.Thread(
        target=_run_retry_background,
        args=(retry_id, skipped, scan_id),
        daemon=True,
    )
    thread.start()

    return jsonify({"scan_id": retry_id})


def _run_retry_background(retry_id: str, skipped: list[dict], original_scan_id: str) -> None:
    """Background thread: retry skipped artists and generate a mini-report."""
    global _active_scan_count

    from spotify_audit.audit_runner import retry_skipped_artists, build_config
    from spotify_audit.reports.formatter import to_html
    from spotify_audit.scoring import build_playlist_report

    def on_progress(phase: str, current: int, total: int, message: str):
        if retry_id in _active_scans:
            _active_scans[retry_id].update({
                "phase": phase,
                "current": current,
                "total": total,
                "message": message,
            })

    try:
        config = build_config()
        artist_reports, still_skipped = retry_skipped_artists(
            skipped=skipped,
            config=config,
            on_progress=on_progress,
        )

        # Build a mini playlist report for the retried artists
        playlist_report = build_playlist_report(
            playlist_name=f"Retry of {len(skipped)} skipped artists",
            playlist_id=f"retry-{original_scan_id}",
            owner="",
            total_tracks=0,
            is_spotify_owned=False,
            artist_reports=artist_reports,
            skipped_artists=still_skipped,
        )
        html = to_html(playlist_report)

        analyzed = len(artist_reports)
        still_count = len(still_skipped)
        if still_count:
            done_msg = f"Retry complete — {analyzed} analyzed, {still_count} still skipped."
        else:
            done_msg = f"Retry complete — all {analyzed} artists analyzed successfully!"

        import json as _json
        _active_scans[retry_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "playlist_name": playlist_report.playlist_name,
            "message": done_msg,
            "skipped_json": _json.dumps(still_skipped) if still_skipped else None,
        })
        scan_store.save(retry_id, _active_scans[retry_id])
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Retry %s failed", retry_id)

        html = _build_error_report(f"retry-{original_scan_id}", exc, tb)
        _active_scans[retry_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "message": f"Retry failed: {exc}",
        })
        scan_store.save(retry_id, _active_scans[retry_id])
    finally:
        with _scans_lock:
            _active_scan_count -= 1


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
