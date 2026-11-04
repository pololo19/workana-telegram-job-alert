"""Run on your home PC where Workana works. The VPS bot calls this over a private network.

Example (Windows, Tailscale):
  set WORKANA_REMOTE_FETCH_SECRET=your-long-random-secret
  set WORKANA_FETCH_BRIDGE_HOST=0.0.0.0
  python fetch_bridge.py

VPS .env:
  WORKANA_REMOTE_FETCH_URL=http://100.x.x.x:8765
  WORKANA_REMOTE_FETCH_SECRET=your-long-random-secret
  WORKANA_USE_PLAYWRIGHT=false
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("workana-bridge")

SECRET = os.environ.get("WORKANA_REMOTE_FETCH_SECRET", "").strip()
HOST = os.environ.get("WORKANA_FETCH_BRIDGE_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("WORKANA_FETCH_BRIDGE_PORT", "8765"))


def _allowed_workana_url(url: str) -> bool:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    return "workana.com" in url.lower()


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "WorkanaFetchBridge/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s - %s", self.client_address[0], fmt % args)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/fetch":
            self.send_error(404, "POST /fetch only")
            return

        if SECRET:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {SECRET}":
                self.send_error(401, "Unauthorized")
                return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            url = str(body.get("url", "")).strip()
        except (ValueError, json.JSONDecodeError, TypeError):
            self.send_error(400, "Invalid JSON body")
            return

        if not _allowed_workana_url(url):
            self.send_error(400, "url must be a workana.com HTTP(S) URL")
            return

        try:
            from workana import (
                WorkanaFetchConfig,
                configure_workana_fetch,
                fetch_page_html,
            )

            configure_workana_fetch(
                WorkanaFetchConfig(
                    use_playwright=False,
                    playwright_fallback=True,
                )
            )
            page_html = asyncio.run(fetch_page_html(url))
        except Exception as exc:
            log.exception("Fetch failed for %s", url)
            payload = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        payload = json.dumps({"html": page_html}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    if not SECRET:
        log.warning(
            "WORKANA_REMOTE_FETCH_SECRET is empty — set a strong secret before "
            "exposing this on a network (e.g. Tailscale)."
        )
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    log.info("Listening on http://%s:%s/fetch", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
