#!/usr/bin/env python3
"""
Serve Spotify Audit web UI as a background daemon.

Usage:
    python web/serve_public.py start             # start on port 80 (background)
    python web/serve_public.py start --port 5000 # custom port
    python web/serve_public.py stop              # stop the daemon
    python web/serve_public.py status            # check if running
    python web/serve_public.py run               # foreground (for debugging)

Pair with ngrok (run separately):
    ngrok http 80

Setup (use a venv on modern distros):
    python3 -m venv .venv
    source .venv/bin/activate
    pip install flask
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PID_FILE = PROJECT_ROOT / "web" / ".server.pid"
LOG_FILE = PROJECT_ROOT / "web" / "server.log"


def _write_pid():
    PID_FILE.write_text(str(os.getpid()))


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # Check if process is actually running
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    return None


def _daemonize():
    """Double-fork to detach from terminal."""
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)

    # Redirect stdio to log file
    sys.stdout.flush()
    sys.stderr.flush()
    log_fd = open(LOG_FILE, "a")
    os.dup2(log_fd.fileno(), sys.stdout.fileno())
    os.dup2(log_fd.fileno(), sys.stderr.fileno())


def cmd_start(port: int, debug: bool):
    existing = _read_pid()
    if existing:
        print(f"  Server already running (PID {existing}). Use 'stop' first.")
        sys.exit(1)

    print(f"  Starting Spotify Audit server on port {port}...")
    print(f"  Log: {LOG_FILE}")

    _daemonize()

    # Now running in background
    _write_pid()

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from web.app import app

    def _shutdown(signum, frame):
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        # Use threaded server for production-ish use
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        PID_FILE.unlink(missing_ok=True)


def cmd_stop():
    pid = _read_pid()
    if not pid:
        print("  Server is not running.")
        return

    print(f"  Stopping server (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for it to die
        for _ in range(20):
            time.sleep(0.25)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    PID_FILE.unlink(missing_ok=True)
    print("  Stopped.")


def cmd_status():
    pid = _read_pid()
    if pid:
        print(f"  Server is running (PID {pid}).")
    else:
        print("  Server is not running.")


def cmd_run(port: int, debug: bool):
    """Run in foreground (for debugging)."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from web.app import app

    print(f"\n  Spotify Audit Web — http://0.0.0.0:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)


def main():
    parser = argparse.ArgumentParser(description="Spotify Audit web server daemon")
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "run"],
        help="start|stop|status|run",
    )
    parser.add_argument("--port", type=int, default=80, help="Port (default: 80)")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args.port, args.debug)
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "status":
        cmd_status()
    elif args.command == "run":
        cmd_run(args.port, args.debug)


if __name__ == "__main__":
    main()
