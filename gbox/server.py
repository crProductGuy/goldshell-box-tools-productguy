"""The local HTTP server behind `gbox serve`.

Serves the dashboard, the CSV the logger writes, the event log, a health
endpoint, and two writes: the dashboard hands its session token to the
service so the poller and watchdog can work without a password on disk, and
it reports what its buttons did so the event log has one line per change.
The dashboard talks to the miner itself; the service never proxies a write.

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

from . import __version__, models, trials

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
MAX_BODY = 8192
MAX_EVENT = 200            # characters of a dashboard-reported event line


def clean_event_text(text):
    """One printable line, at most MAX_EVENT characters; "" if there is nothing usable.

    Anyone who can reach the server can post here, so the log line can never
    carry a newline (a forged second line) or a control character.
    """
    if not isinstance(text, str):
        return ""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(cleaned.split())[:MAX_EVENT]


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
        self.trials_cache = (None, None)     # ((mtime_ns, size) of log.csv, table) so a refresh does not re-parse

    def trials_table(self):
        """The clock-trials table, recomputed only when log.csv changed."""
        path = self.data_dir / "log.csv"
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        with self.lock:
            if self.trials_cache[0] != key or self.trials_cache[1] is None:
                self.trials_cache = (key, trials.table(path))
            return self.trials_cache[1]

    def trial_progress(self):
        """The runner's progress file (`gbox trials run` writes it, removes it when done), or {}."""
        try:
            with open(self.data_dir / "trial.json", encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
        except (OSError, ValueError):
            return {}

    def health(self):
        p, w = self.poller, self.watchdog
        model = ((p.miner_status if p else None) or {}).get("model")
        return {
            "ok": True, "version": __version__, "host": self.cfg.host,
            "has_token": self.miner.has_token, "can_login": self.miner.can_login,
            "poll_interval": self.cfg.poll_interval,
            "model": model, "rated": models.rated_for(model),
            "samples": p.samples if p else 0, "errors": p.errors if p else 0,
            "last_error": p.last_error if p else None,
            "latest_time": (p.latest or {}).get("time") if p else None,
            "watchdog": {
                "enabled": w is not None,
                "restarts_today": w.restarts_today() if w else 0,
                "last_reason": w.last_reason if w else None,
            },
            "power": self.power_health(),
        }

    def power_health(self):
        """The plug as the poller last saw it; nothing here queries the plug."""
        p, w = self.poller, self.watchdog
        cfg = self.cfg.power
        info = (p.plug_info if p else None) or {}
        state = p.plug_state if p else None
        return {
            "configured": cfg is not None,
            "model": info.get("model"),
            "alias": info.get("alias"),
            "meter": bool(info.get("meter")),
            "state": {True: "on", False: "off"}.get(state),
            "watts": p.plug_watts if p else None,
            "cycle": bool(cfg.get("cycle")) if cfg else False,
            "cycles_today": w.cycles_today() if w and hasattr(w, "cycles_today") else 0,
            "last_reason": getattr(w, "last_power_reason", None) if w else None,
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
            if path == "/api/trials":
                return self._json(200, state.trials_table())
            if path == "/api/trial":
                return self._json(200, state.trial_progress())
            self._send(404, "not found")

        def _json_body(self):
            """The POST body as parsed JSON, or None after an error reply has been sent."""
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                self._send(415, "expected application/json")
                return None
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                self._send(413, "bad length")
                return None
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                self._send(400, "bad json")
                return None
            if not isinstance(body, dict):
                self._send(400, "expected a JSON object")
                return None
            return body

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/token":
                return self._post_token()
            if path == "/api/event":
                return self._post_event()
            self._send(404, "not found")

        def _post_token(self):
            body = self._json_body()
            if body is None:
                return
            try:
                state.miner.set_token(body["token"])
            except (ValueError, KeyError, TypeError):
                return self._send(400, "expected {\"token\": \"<jwt>\"}")
            if state.events and not getattr(state, "_token_announced", False):
                state.events.write("service: session token received from the dashboard")
                state._token_announced = True
            self._send(204)

        def _post_event(self):
            body = self._json_body()
            if body is None:
                return
            msg = clean_event_text(body.get("message"))
            if not msg:
                return self._send(400, "expected {\"message\": \"<text>\"}")
            if state.events:
                state.events.write("dashboard: " + msg)
            if body.get("restart") is True and state.watchdog is not None:
                state.watchdog.external_restart()
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
