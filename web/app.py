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
import sys
import threading
import time
import uuid
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, render_template

from spotify_audit.audit_runner import run_audit, build_config
from spotify_audit.reports.formatter import to_html

app = Flask(__name__, template_folder="templates", static_folder="static")
logger = logging.getLogger("spotify_audit.web")

# In-memory scan store
# {scan_id: {"status": str, "phase": str, "current": int, "total": int,
#             "message": str, "result_html": str|None, "error": str|None,
#             "started_at": float, "playlist_name": str|None}}
scans: dict[str, dict] = {}


def _run_scan_background(scan_id: str, playlist_url: str, tier: str) -> None:
    """Background thread: run the audit and store results."""
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
            max_tier=tier,
            config=config,
            on_progress=on_progress,
        )
        html = to_html(playlist_report)
        scans[scan_id].update({
            "status": "complete",
            "phase": "done",
            "result_html": html,
            "playlist_name": playlist_report.playlist_name,
            "message": f"Done! Analyzed {playlist_report.total_unique_artists} artists.",
        })
    except Exception as exc:
        logger.exception("Scan %s failed", scan_id)
        scans[scan_id].update({
            "status": "error",
            "phase": "error",
            "error": str(exc),
            "message": f"Error: {exc}",
        })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    data = request.get_json(silent=True) or {}
    playlist_url = data.get("url", "").strip()
    tier = data.get("tier", "quick").strip().lower()

    if not playlist_url:
        return jsonify({"error": "Please provide a playlist URL"}), 400

    # Accept various Spotify URL formats
    if "spotify.com" not in playlist_url and "spotify:" not in playlist_url:
        return jsonify({"error": "Please provide a valid Spotify playlist URL"}), 400

    if tier not in ("quick", "standard", "deep"):
        tier = "quick"

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

    thread = threading.Thread(
        target=_run_scan_background,
        args=(scan_id, playlist_url, tier),
        daemon=True,
    )
    thread.start()

    return jsonify({"scan_id": scan_id})


@app.route("/api/scan/<scan_id>")
def scan_status(scan_id):
    scan = scans.get(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404

    # Don't send the full HTML in status polls
    resp = {k: v for k, v in scan.items() if k != "result_html"}
    resp["has_result"] = scan["result_html"] is not None
    elapsed = time.time() - scan["started_at"]
    resp["elapsed_seconds"] = round(elapsed, 1)
    return jsonify(resp)


@app.route("/report/<scan_id>")
def view_report(scan_id):
    scan = scans.get(scan_id)
    if not scan or scan["status"] != "complete" or not scan["result_html"]:
        return "Report not ready yet", 404
    return scan["result_html"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spotify Audit Web Server")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print(f"\n  Spotify Audit Web — http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
