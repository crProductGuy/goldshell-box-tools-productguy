"""The local HTTP server behind `gbox serve`.

Serves the dashboard, the CSV the logger writes, the event log, a health
endpoint, and the one write: the dashboard hands its session token to the
service so the poller and watchdog can work without a password on disk.

Binds to 127.0.0.1 unless told otherwise. Never logs a request line: the
miner login URL carries the encrypted password, and tokens are password-
equivalent on this firmware.
"""
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
MAX_BODY = 8192


class ServiceState:
    """What the handlers may look at. One instance per running service."""

    def __init__(self, cfg, miner, data_dir, poller=None, watchdog=None, events=None):
        self.cfg = cfg
        self.miner = miner
        self.data_dir = Path(data_dir)
        self.poller = poller
        self.watchdog = watchdog
        self.events = events
        self.lock = threading.Lock()

    def health(self):
        p, w = self.poller, self.watchdog
        return {
            "ok": True, "version": __version__, "host": self.cfg.host,
            "has_token": self.miner.has_token, "can_login": self.miner.can_login,
            "poll_interval": self.cfg.poll_interval,
            "samples": p.samples if p else 0, "errors": p.errors if p else 0,
            "last_error": p.last_error if p else None,
            "latest_time": (p.latest or {}).get("time") if p else None,
            "watchdog": {
                "enabled": w is not None,
                "restarts_today": w.restarts_today() if w else 0,
                "last_reason": w.last_reason if w else None,
            },
        }


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "gbox/" + __version__
        sys_version = ""

        def log_message(self, format, *args):
            pass

        def log_error(self, format, *args):
            pass

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", cache=False):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj), "application/json")

        def _file(self, path, ctype=None):
            path = Path(path)
            if not path.is_file():
                return self._send(404, "not found")
            with open(path, "rb") as f:
                body = f.read()
            self._send(200, body, ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                return self._file(WEB_DIR / STATIC[path])
            if path == "/api/health":
                return self._json(200, state.health())
            if path == "/api/log.csv":
                return self._file(state.data_dir / "log.csv", "text/csv; charset=utf-8")
            if path == "/api/events":
                lines = state.events.tail(200) if state.events else []
                return self._send(200, "".join(lines))
            if path == "/api/latest":
                latest = state.poller.latest if state.poller else None
                return self._json(200, latest or {})
            self._send(404, "not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/api/token":
                return self._send(404, "not found")
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(415, "expected application/json")
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                return self._send(413, "bad length")
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                state.miner.set_token(body["token"])
            except (ValueError, KeyError, TypeError):
                return self._send(400, "expected {\"token\": \"<jwt>\"}")
            if state.events and not getattr(state, "_token_announced", False):
                state.events.write("service: session token received from the dashboard")
                state._token_announced = True
            self._send(204)

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that is already listening,
    # which would silently run two services. Exclusive there; on POSIX keep the fast restart.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def make_server(state):
    """A ThreadingHTTPServer bound per the config. Raises OSError if the port is taken."""
    return _Server((state.cfg.bind, state.cfg.port), make_handler(state))
