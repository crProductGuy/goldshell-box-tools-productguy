"""The local HTTP server behind `gbox serve`.

Serves the dashboard, the CSV the logger writes, the event log, a health
endpoint, and a few writes: the dashboard hands its session token to the
service so the poller and watchdog can work without a password on disk, it
reports what its buttons did so the event log has one line per change, and
since 0.5.0 it can start or release a hold and switch the plug (off and
cycle only with the miner's password, checked by a login). The dashboard
talks to the miner itself; the service never proxies a settings write.

Binds to 127.0.0.1 unless told otherwise. Never logs a request line: the
miner login URL carries the encrypted password, and tokens are password-
equivalent on this firmware.
"""
import json
import mimetypes
import os
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import urllib.parse

from . import __version__, api, models, series, trials
from .power import PowerRefused

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
MAX_BODY = 8192
MAX_EVENT = 500            # characters of a dashboard-reported event line (200 cut hand-written notes; 2026-09-12)
HOLD_DEFAULT_MINUTES = 60  # a hold with no `minutes` given; long enough for a swap, short enough to notice
HOLD_MAX_MINUTES = 1440


def _int_in(value, lo, hi):
    """`value` if it is an int (not a bool) within [lo, hi], else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi else None


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

    def __init__(self, cfg, miner, data_dir, poller=None, watchdog=None, events=None, power_control=None):
        self.cfg = cfg
        self.miner = miner
        self.data_dir = Path(data_dir)
        self.poller = poller
        self.watchdog = watchdog
        self.events = events
        self.power_control = power_control   # gbox.power.PowerControl when a plug is configured
        self.lock = threading.Lock()
        self.trials_cache = (None, None)     # ((mtime_ns, size) of log.csv, table) so a refresh does not re-parse
        self.series_cache = {}               # (hours, bucket) -> ((mtime_ns, size), result), same idea for the charts

    def _log_key(self):
        try:
            st = (self.data_dir / "log.csv").stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def series(self, hours, bucket):
        """The bucketed log for the charts (docs/charts-proposal.md), recomputed only when log.csv changed."""
        key = self._log_key()
        with self.lock:
            cached = self.series_cache.get((hours, bucket))
            if cached and cached[0] == key:
                return cached[1]
            rows = series.read_rows(self.data_dir / "log.csv")
            lines = self.events.tail(4000) if self.events else []
            result = series.buckets(rows, hours, bucket, events=lines)
            self.series_cache[(hours, bucket)] = (key, result)
            return result

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
            "profile": models.profile_for(model) if model else None,     # the capability record (0.7.0), None before the first good poll
            "samples": p.samples if p else 0, "errors": p.errors if p else 0,
            "last_error": p.last_error if p else None,
            "latest_time": (p.latest or {}).get("time") if p else None,
            "watchdog": {
                "enabled": w is not None,
                "restarts_today": w.restarts_today() if w else 0,
                "last_reason": w.last_reason if w else None,
            },
            "power": self.power_health(),
            "ladder": self.ladder(),
            "temps": dict(self.cfg.temps),           # 0.7.0: the hottest-chip thresholds on the sustained level
            "syslog_interval": self.cfg.syslog_interval,
            "hold": w.hold_info() if w else None,
        }

    def ladder(self):
        """The watchdog's timings and caps as configured, and the file they live in, so the page can say
        what will happen to a hung miner and where to change it. Power fields are None without a plug."""
        w, p = self.cfg.watchdog, self.cfg.power or {}
        return {"stall_minutes": w["stall_minutes"], "unreachable_minutes": w["unreachable_minutes"],
                "min_gap_minutes": w["min_gap_minutes"], "max_restarts_per_day": w["max_restarts_per_day"],
                "after_minutes": p.get("after_minutes"), "settle_minutes": p.get("settle_minutes"),
                "max_cycles_per_day": p.get("max_cycles_per_day"), "idle_watts": p.get("idle_watts"),
                "off_seconds": p.get("off_seconds"), "schedule": p.get("schedule"),
                "boot_watts": p.get("boot_watts"), "boot_check_minutes": p.get("boot_check_minutes"),
                "config_path": str(self.data_dir / "config.json")}

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
            "off_by_you": bool(self.power_control.off_by_you) if self.power_control else False,
            "busy": self.power_control.busy if self.power_control else None,
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
                return self._csv()
            if path == "/api/events":
                lines = state.events.tail(2000) if state.events else []   # weeks of quiet operation; the interventions table reads it all
                return self._send(200, "".join(lines))
            if path == "/api/latest":
                latest = state.poller.latest if state.poller else None
                return self._json(200, latest or {})
            if path == "/api/boards":
                latest = state.poller.latest if state.poller else None
                return self._json(200, (latest or {}).get("_boards") or [])
            if path == "/api/trials":
                return self._json(200, state.trials_table())
            if path == "/api/trial":
                return self._json(200, state.trial_progress())
            if path == "/api/series":
                return self._get_series()
            self._send(404, "not found")

        def _csv(self):
            """log.csv whole, or its header plus the last `tail` rows (the page asks for a day's worth for the tiles)."""
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            path = state.data_dir / "log.csv"
            if "tail" not in q:
                return self._file(path, "text/csv; charset=utf-8")
            try:
                n = int(q["tail"][0])
                if n < 1:
                    raise ValueError
            except ValueError:
                return self._send(400, "tail must be a positive whole number")
            if not path.is_file():
                return self._send(404, "not found")
            with open(path, encoding="utf-8", errors="replace") as f:
                header = f.readline()
                rows = deque(f, maxlen=n)
            self._send(200, header + "".join(rows), "text/csv; charset=utf-8")

        def _get_series(self):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                hours = int(q.get("hours", ["24"])[0])
                bucket = int(q.get("bucket", ["5"])[0])
                if not (series.MIN_HOURS <= hours <= series.MAX_HOURS and series.MIN_BUCKET <= bucket <= series.MAX_BUCKET):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "hours must be %d to %d and bucket %d to %d minutes"
                                        % (series.MIN_HOURS, series.MAX_HOURS, series.MIN_BUCKET, series.MAX_BUCKET)})
            self._json(200, state.series(hours, bucket))

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
            if path == "/api/hold":
                return self._post_hold()
            if path == "/api/hold/release":
                return self._post_hold_release()
            if path == "/api/power":
                return self._post_power()
            self._send(404, "not found")

        def _error(self, code, text):
            self._json(code, {"error": text})

        # -- holds and planned power (docs/power-hold-proposal.md)

        def _post_hold(self):
            body = self._json_body()
            if body is None:
                return
            if state.watchdog is None:
                return self._error(409, "the watchdog is off for this run (--no-watchdog), so there is nothing to hold")
            minutes = body.get("minutes", HOLD_DEFAULT_MINUTES)
            if minutes is not None and _int_in(minutes, 1, HOLD_MAX_MINUTES) is None:
                return self._error(400, "minutes must be 1 to %d, or null for no expiry" % HOLD_MAX_MINUTES)
            reason = clean_event_text(body.get("reason")) if body.get("reason") is not None else ""
            state.watchdog.hold_start(minutes, reason, "page")
            self._json(200, {"hold": state.watchdog.hold_info()})

        def _post_hold_release(self):
            if (self.headers.get("Content-Length") or "0") != "0" and self._json_body() is None:
                return
            if state.watchdog is None:
                return self._error(409, "the watchdog is off for this run (--no-watchdog), so there is no hold")
            state.watchdog.hold_release("you")
            self._json(200, {"hold": None})

        def _post_power(self):
            body = self._json_body()
            if body is None:
                return
            pc = state.power_control
            if pc is None:
                return self._error(409, "no plug configured: `gbox power discover`, then `gbox power init --plug <address>`, "
                                        "then restart the service")
            action = body.get("action")
            if action not in ("off", "on", "cycle"):
                return self._error(400, "action must be off, on or cycle")
            off_seconds = body.get("off_seconds")
            if off_seconds is not None and _int_in(off_seconds, 3, 120) is None:
                return self._error(400, "off_seconds must be 3 to 120")
            if action != "on":
                hexpw = body.get("password_hex")
                if not isinstance(hexpw, str) or not hexpw:
                    return self._error(400, "password_hex is required to switch off or cycle")
                try:
                    state.miner.verify_password_hex(hexpw)
                except api.AuthError:
                    return self._error(403, "the miner rejected that password; nothing sent")
                except api.MinerError:
                    return self._error(409, "the miner is not answering, so the password cannot be checked; nothing sent. "
                                            "A frozen miner is the watchdog's job (it cycles the plug on its own); "
                                            "for a cycle by hand run `gbox power cycle` from a terminal")
            try:
                if action == "off":
                    st = pc.off("page")
                elif action == "on":
                    st = pc.on("page")
                else:
                    st = pc.cycle("page", off_seconds)
            except PowerRefused as e:
                return self._error(409, str(e))
            self._json(200, {"power": st, "hold": st["hold"]})

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
