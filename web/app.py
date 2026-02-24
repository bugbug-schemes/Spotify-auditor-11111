"""
Spotify Audit — Web Interface

A Flask app that lets users paste a Spotify playlist URL
and get an HTML report back, with real-time progress updates.

Scan state is persisted to SQLite (via ScanStore) so that status
polls survive gunicorn worker restarts, OOM kills, and other
transient failures common on free-tier hosting.

Usage:
    python web/app.py                  # dev server on port 5000
    python web/app.py --port 8080      # custom port
"""

from __future__ import annotations

import argparse
import logging
import os
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

from web.api import cms_api, init_db
from web.scan_store import ScanStore

app = Flask(__name__, template_folder="templates", static_folder="static")
app.register_blueprint(cms_api)
logger = logging.getLogger("spotify_audit.web")

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

MAX_CONCURRENT_SCANS = 5

# In-memory cache for fast progress updates during active scans.
# The ScanStore (SQLite) is the source of truth and survives restarts.
_active_scans: OrderedDict[str, dict] = OrderedDict()

# Persistent scan store — initialized at module level so gunicorn
# workers each get their own connection but share the same DB file.
scan_store = ScanStore()


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
    # Lazy-import heavy scan machinery so the Flask app starts instantly.
    # These imports pull in 13+ API clients and ML modules — too slow for
    # worker init on Render's free tier where CPU is limited.
    from spotify_audit.audit_runner import run_audit, build_config
    from spotify_audit.reports.formatter import to_html

    # Track last heartbeat write to avoid hammering SQLite on every tick
    last_db_write = time.time()
    HEARTBEAT_INTERVAL = 10  # seconds between SQLite writes

    def on_progress(phase: str, current: int, total: int, message: str):
        nonlocal last_db_write
        # Always update the fast in-memory cache
        if scan_id in _active_scans:
            _active_scans[scan_id].update({
                "phase": phase,
                "current": current,
                "total": total,
                "message": message,
            })

        # Periodically persist to SQLite (heartbeat)
        now = time.time()
        if now - last_db_write >= HEARTBEAT_INTERVAL:
            try:
                scan_store.heartbeat(scan_id,
                                     phase=phase, current=current,
                                     total=total, message=message)
            except Exception:
                logger.debug("Heartbeat write failed for %s", scan_id)
            last_db_write = now

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

        # Update in-memory cache
        if scan_id in _active_scans:
            _active_scans[scan_id].update({
                "status": "complete",
                "phase": "done",
                "result_html": html,
                "playlist_name": playlist_report.playlist_name,
                "message": done_msg,
            })

        # Persist to SQLite
        scan_store.mark_complete(
            scan_id, result_html=html,
            playlist_name=playlist_report.playlist_name,
            message=done_msg,
        )
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Scan %s failed", scan_id)

        # Even on total failure, try to generate a minimal error report
        html = _build_error_report(playlist_url, exc, tb)
        error_str = str(exc)

        if scan_id in _active_scans:
            _active_scans[scan_id].update({
                "status": "complete",
                "phase": "done",
                "result_html": html,
                "message": f"Scan failed: {error_str}",
            })

        scan_store.mark_complete(
            scan_id, result_html=html,
            playlist_name=None,
            message=f"Scan failed: {error_str}",
        )
    finally:
        # Clean up in-memory entry after a delay so the frontend
        # has time to see the final status before it's removed.
        def _deferred_cleanup():
            time.sleep(120)
            _active_scans.pop(scan_id, None)

        threading.Thread(target=_deferred_cleanup, daemon=True).start()


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("index.html"), 404


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
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    data = request.get_json(silent=True) or {}
    playlist_url = data.get("url", "").strip()
    deep = bool(data.get("deep", False))

    if not playlist_url:
        return jsonify({"error": "Please provide a playlist URL"}), 400

    # Accept various Spotify URL formats
    if "spotify.com" not in playlist_url and "spotify:" not in playlist_url:
        return jsonify({"error": "Please provide a valid Spotify playlist URL"}), 400

    # Check concurrent scan limit (uses DB count for accuracy across restarts)
    active = scan_store.count_active()
    if active >= MAX_CONCURRENT_SCANS:
        return jsonify({
            "error": f"Server busy — {active} scans running. Please try again shortly."
        }), 503

    scan_id = uuid.uuid4().hex[:8]
    now = time.time()

    # Persist to SQLite first (source of truth)
    scan_store.create(scan_id)

    # Also cache in-memory for fast progress polling
    _active_scans[scan_id] = {
        "status": "running",
        "phase": "starting",
        "current": 0,
        "total": 0,
        "message": "Starting scan...",
        "result_html": None,
        "error": None,
        "started_at": now,
        "playlist_name": None,
    }

    # Clean up old DB entries periodically
    try:
        scan_store.cleanup_old()
    except Exception:
        pass

    thread = threading.Thread(
        target=_run_scan_background,
        args=(scan_id, playlist_url, deep),
        daemon=True,
    )
    thread.start()

    return jsonify({"scan_id": scan_id})


@app.route("/api/scan/<scan_id>")
def scan_status(scan_id):
    # Always check SQLite first — it is the source of truth and has
    # stale/timeout detection that catches hung scan threads.
    scan = scan_store.get(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404

    # If SQLite says the scan is still running, overlay fresher progress
    # data from the in-memory cache (updated on every progress tick).
    if scan["status"] == "running":
        cached = _active_scans.get(scan_id)
        if cached:
            if cached["status"] in ("complete", "error"):
                # Thread finished but SQLite hasn't been updated yet — use in-memory
                scan = dict(cached)
            else:
                # Thread is alive — use in-memory progress (more current than
                # the SQLite heartbeats which only write every 10s)
                scan["phase"] = cached.get("phase", scan["phase"])
                scan["current"] = cached.get("current", scan["current"])
                scan["total"] = cached.get("total", scan["total"])
                scan["message"] = cached.get("message", scan["message"])
    elif scan["status"] == "error" and scan_id in _active_scans:
        # SQLite detected stale/timeout — sync to in-memory so the
        # deferred cleanup thread and report endpoint stay consistent.
        _active_scans[scan_id].update({
            "status": scan["status"],
            "phase": scan.get("phase", "error"),
            "error": scan.get("error", ""),
            "message": scan.get("message", ""),
        })

    resp = {k: v for k, v in scan.items() if k not in ("result_html",)}
    resp["has_result"] = scan.get("result_html") is not None
    elapsed = time.time() - scan["started_at"]
    resp["elapsed_seconds"] = round(elapsed, 1)
    return jsonify(resp)


@app.route("/report/<scan_id>")
def view_report(scan_id):
    # Check in-memory first
    cached = _active_scans.get(scan_id)
    if cached and cached["status"] == "complete" and cached["result_html"]:
        return cached["result_html"]

    # Fall back to SQLite
    html = scan_store.get_result_html(scan_id)
    if html:
        return html

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
