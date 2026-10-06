"""Simulated Linux service node.

One image, three roles (via TARGET_ROLE): http (serves /health), tcp
(bare TCP listener), http+chaos (adds failure endpoints for injection).

Failure injection endpoints (used by scripts/failure_injection):
  POST /fail      -> /health starts returning 503   (app-level failure)
  POST /recover   -> /health returns 200 again
  POST /crash     -> exits the process immediately  (process-level failure)

Endpoints are POST-only and bound to the internal network; nothing is
published to the host. This keeps injection explicit and reproducible
instead of requiring exec into containers.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = os.environ.get("TARGET_ROLE", "http")
PORT = int(os.environ.get("TARGET_PORT", "8080"))

state = {"healthy": True}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/ready"):
            if state["healthy"]:
                self._send(200, b'{"status": "ok"}')
            else:
                self._send(503, b'{"status": "failing (injected)"}')
        elif self.path == "/":
            self._send(200, b'{"service": "sim-node", "role": "%s"}' % ROLE.encode())
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self):  # noqa: N802
        if ROLE != "http+chaos":
            self._send(403, b'{"error": "injection disabled on this node"}')
            return
        if self.path == "/fail":
            state["healthy"] = False
            self._send(200, b'{"action": "health failing"}')
        elif self.path == "/recover":
            state["healthy"] = True
            self._send(200, b'{"action": "health restored"}')
        elif self.path == "/crash":
            self._send(200, b'{"action": "crashing"}')
            sys.stdout.flush()
            os._exit(1)
        else:
            self._send(404, b'{"error": "not found"}')

    def log_message(self, *args):  # noise reduction; access logs go to stdout anyway
        pass


def serve_tcp() -> None:
    """Bare TCP listener role (simulates a database port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", PORT))
        srv.listen(16)
        while True:
            conn, _ = srv.accept()
            conn.close()  # accept + close: the port answers, like a real DB port check


if __name__ == "__main__":
    if ROLE == "tcp":
        serve_tcp()
    else:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print(f"sim-node role={ROLE} port={PORT}", flush=True)
        server.serve_forever()
