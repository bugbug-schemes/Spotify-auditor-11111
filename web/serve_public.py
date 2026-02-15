#!/usr/bin/env python3
"""
Serve Spotify Audit web UI publicly via ngrok.

One command to expose the web interface to the internet:

    python web/serve_public.py                # default port 5000
    python web/serve_public.py --port 8080    # custom port

Requirements:
    pip install pyngrok flask

First-time setup:
    1. Sign up free at https://dashboard.ngrok.com/signup
    2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
    3. Run: ngrok config add-authtoken YOUR_TOKEN
       — or — set env var: NGROK_AUTHTOKEN=YOUR_TOKEN
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Serve Spotify Audit publicly via ngrok")
    parser.add_argument("--port", type=int, default=5000, help="Local port (default: 5000)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Set authtoken from env if available
    token = os.environ.get("NGROK_AUTHTOKEN")

    try:
        from pyngrok import conf, ngrok
    except ImportError:
        print("pyngrok is required: pip install pyngrok")
        sys.exit(1)

    if token:
        conf.get_default().auth_token = token

    # Import and configure Flask app
    from web.app import app

    # Open ngrok tunnel
    print(f"\n  Starting ngrok tunnel to port {args.port}...")
    try:
        tunnel = ngrok.connect(args.port)
    except Exception as e:
        print(f"\n  Failed to start ngrok tunnel: {e}")
        print("\n  Troubleshooting:")
        print("    1. Sign up at https://dashboard.ngrok.com/signup")
        print("    2. Set your authtoken:")
        print("       export NGROK_AUTHTOKEN=your_token_here")
        print("    3. Or run: ngrok config add-authtoken YOUR_TOKEN")
        sys.exit(1)

    public_url = tunnel.public_url
    print(f"""
  ╔══════════════════════════════════════════════════════════╗
  ║  Spotify Audit Web — PUBLIC                             ║
  ║                                                         ║
  ║  Local:   http://localhost:{args.port:<25}║
  ║  Public:  {public_url:<46}║
  ║                                                         ║
  ║  Share the public URL with anyone!                      ║
  ║  Press Ctrl+C to stop.                                  ║
  ╚══════════════════════════════════════════════════════════╝
""")

    try:
        app.run(host="127.0.0.1", port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Shutting down ngrok tunnel...")
        ngrok.kill()


if __name__ == "__main__":
    main()
