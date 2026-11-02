"""An HTTP stand-in for a Goldshell Box miner, serving the sanitized fixtures.

Used by the unit tests and by hand:

    python -m tests.fake_miner --port 8999
    gbox --host 127.0.0.1:8999 status      (password: "password")

It reproduces the parts of the firmware the tools depend on: the login
handshake with the encrypted password, Bearer-token checks, the settings
PUT that changes the plan, the restart PUT, and CORS headers so the
dashboard can be pointed at it. Knobs on the instance let tests inject 401s
and count concurrent requests.
"""
import argparse
import json
import os
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gbox import aes

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.ZmFrZS1zaWduYXR1cmU"


def _read(base, name):
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return f.read()


def _read_opt(base, name):
    """`_read`, or None when the fixture doesn't have this file (e.g. sc5proii has no dbg_icinfo.json)."""
    path = os.path.join(base, name)
    return _read(base, name) if os.path.exists(path) else None


class _Devs4028Handler(socketserver.BaseRequestHandler):
    """Answers the one `{"command":"devs"}` request cgminer-style port 4028 gets, with the fixture's captured
    reply (NUL-terminated, as the firmware sends it), then closes."""

    def handle(self):
        start = time.monotonic()
        try:
            raw = self.request.recv(4096)
        except OSError:
            raw = b""
        payload = self.server.payload
        owner = getattr(self.server, "owner", None)
        command = None
        if owner is not None:
            command = owner._command4028(raw)
            if owner.study:
                payload = owner._study_4028(command, payload)
        self.request.sendall(payload)
        if owner is not None:
            owner._log("4028", "", command, start, time.monotonic(), False)


class _Devs4028Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeMiner:
    def __init__(self, host="127.0.0.1", port=0, password="password", delay=0.0,
                 fixtures=None, port4028=False, dbg_locked_icinfo=False, study=False):
        self.dir = os.path.join(FIX, fixtures) if fixtures else FIX
        self.password_hex = aes.encrypt_password(password)
        self.delay = delay                      # seconds each request takes; makes overlaps visible
        self.setting = json.loads(_read(self.dir, "mcb_setting.json"))
        self.status = json.loads(_read(self.dir, "mcb_status.json"))
        self.minerinfo = _read(self.dir, "dbg_minerinfo.txt")
        self.icinfo = _read_opt(self.dir, "dbg_icinfo.json")
        self.history = _read(self.dir, "cpb_hshistory.json")
        self.syslog = _read_opt(self.dir, "dbg_minersyslog.txt")     # the cgminer log; absent on some captures
        self.dbg_locked_icinfo = dbg_locked_icinfo   # True: /dbg/icinfo always 401s "Debug access is locked"
        # Study mode (tools/hs_probe.py tests), off by default so every existing test sees the old fake:
        # answers each port-4028 command from api4028_<command>.json (falling back to the devs reply),
        # serves /mcb/pools, /mcb/wifisetting and /dbg/syslog from their fixtures, answers /mcb/cgminer
        # and /dbg/vsinfo with 500, and answers GET /mcb/protect and /mcb/defend with 404 (the stock UI
        # only PUTs them; whether a GET is answered is unknown, so the fake says "not found").
        self.study = study
        self.fail_pools = False                 # study: /mcb/pools answers 500 and 4028 `pools` answers an empty reply
        self.unauthorized_paths = set()         # authed requests to these paths always get 401
        self.request_log = []                   # one dict per request, HTTP and 4028 alike (see _log)
        self.pools = _read_opt(self.dir, "mcb_pools.json")
        self.wifisetting = _read_opt(self.dir, "mcb_wifisetting.json")
        self.syslog_dbg = _read_opt(self.dir, "dbg_syslog.txt")
        self.restarts = 0
        self.logins = 0
        self.requests = []                      # (method, path) in arrival order
        self.seen_headers = []                  # (method, path, the Authorization header or None)
        self.unauthorized_next = 0              # answer 401 to this many upcoming authed requests
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        outer = self

        self._devs4028 = None
        self._devs4028_thread = None
        self.devs4028_port = None
        if port4028:
            payload = (_read_opt(self.dir, "api4028_devs.json") or "{}").encode("utf-8") + b"\x00"
            self._devs4028 = _Devs4028Server((host, 0), _Devs4028Handler)
            self._devs4028.payload = payload
            self._devs4028.owner = outer
            self.devs4028_port = self._devs4028.server_address[1]

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")

            def _reply(self, code, body=b"", ctype="application/json"):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self._reply(204)

            def _enter(self):
                self._entry = outer._log("http", self.command, self.path.split("?")[0], time.monotonic(), None,
                                         self.headers.get("Authorization") is not None)
                with outer._lock:
                    outer.in_flight += 1
                    outer.max_in_flight = max(outer.max_in_flight, outer.in_flight)
                    outer.requests.append((self.command, self.path.split("?")[0]))
                    outer.seen_headers.append((self.command, self.path.split("?")[0], self.headers.get("Authorization")))
                if outer.delay:
                    time.sleep(outer.delay)

            def _leave(self):
                self._entry["end"] = time.monotonic()
                with outer._lock:
                    outer.in_flight -= 1

            def _authed(self):
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return False
                if self.path.split("?")[0] in outer.unauthorized_paths:
                    return False
                with outer._lock:
                    if outer.unauthorized_next > 0:
                        outer.unauthorized_next -= 1
                        return False
                return True

            def do_GET(self):
                self._enter()
                try:
                    url = urllib.parse.urlparse(self.path)
                    if url.path == "/user/login":
                        q = urllib.parse.parse_qs(url.query)
                        outer.logins += 1
                        if q.get("password", [""])[0] == outer.password_hex and q.get("cipher") == ["true"]:
                            return self._reply(200, json.dumps({"JWT Token": TOKEN}))
                        return self._reply(200, json.dumps({"code": 1, "msg": "password error"}))
                    if url.path == "/dbg/icinfo" and outer.dbg_locked_icinfo:
                        return self._reply(401, "Debug access is locked", "text/plain")
                    if url.path == "/mcb/status" and self.headers.get("Authorization") is None:
                        # The firmware answers this one without a token (verified on the
                        # SC-BOX, 2026-09-19). It is what `gbox discover` probes with.
                        return self._reply(200, json.dumps(outer.status))
                    if not self._authed():
                        return self._reply(401, "Check Token Error", "text/plain")
                    if outer.study:
                        special = outer._study_get(url.path)
                        if special is not None:
                            return self._reply(*special)
                    routes = {
                        "/mcb/status": lambda: json.dumps(outer.status),
                        "/mcb/setting": lambda: json.dumps(outer.setting),
                        "/dbg/minerinfo": lambda: outer.minerinfo,
                        "/dbg/icinfo": lambda: outer.icinfo,
                        "/cpb/hshistory": lambda: outer.history,
                        "/dbg/minersyslog": lambda: outer.syslog,
                        "/dbg/fanctrllog": lambda: "Fans Change (fan0: 62 ==> 61) reason(t:64.2 acc:0.0 target_temp:65)\n",
                    }
                    if url.path in routes:
                        data = routes[url.path]()
                        if data is None:
                            return self._reply(404, "not found", "text/plain")
                        return self._reply(200, data, "text/plain" if url.path.startswith("/dbg") else "application/json")
                    return self._reply(404, "not found", "text/plain")
                finally:
                    self._leave()

            def do_PUT(self):
                self._enter()
                try:
                    if not self._authed():
                        return self._reply(401, "Check Token Error", "text/plain")
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n) if n else b""
                    path = self.path.split("?")[0]
                    if path == "/mcb/setting":
                        outer.setting = json.loads(raw.decode("utf-8"))
                        return self._reply(200, json.dumps({"code": 0}))
                    if path == "/mcb/restart":
                        outer.restarts += 1
                        return self._reply(200, json.dumps({"code": 0}))
                    return self._reply(404, "not found", "text/plain")
                finally:
                    self._leave()

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address[:2]
        self._thread = None

    def _log(self, kind, method, target, start, end, auth):
        """Append one entry to `request_log`: kind "http" or "4028", the HTTP method (empty for 4028), the
        path without its query or the 4028 command, monotonic start and end, whether an Authorization
        header was sent. Returns the (mutable) entry so the HTTP handler can fill `end` in."""
        entry = {"kind": kind, "method": method, "target": target, "start": start, "end": end, "auth": auth}
        with self._lock:
            self.request_log.append(entry)
        return entry

    @staticmethod
    def _command4028(raw):
        try:
            return str(json.loads(raw.decode("utf-8", "replace")).get("command"))
        except (ValueError, AttributeError):
            return None

    def _study_4028(self, command, fallback):
        """Study mode: the fixture file for this command, else the devs reply. Never acts on a command."""
        if command == "pools" and self.fail_pools:
            return b"\x00"
        if command and command.isalpha():
            text = _read_opt(self.dir, "api4028_%s.json" % command)
            if text is not None:
                return text.rstrip("\n").encode("utf-8") + b"\x00"
        return fallback

    def _study_get(self, path):
        """Study mode GET routes as (code, body, content type), or None to fall through to the normal ones."""
        if path in ("/mcb/cgminer", "/dbg/vsinfo"):
            return 500, "internal error", "text/plain"
        if path in ("/mcb/protect", "/mcb/defend"):
            return 404, "not found", "text/plain"
        if path == "/mcb/pools":
            if self.fail_pools or self.pools is None:
                return 500, "internal error", "text/plain"
            return 200, self.pools, "application/json"
        if path == "/mcb/wifisetting" and self.wifisetting is not None:
            return 200, self.wifisetting, "application/json"
        if path == "/dbg/syslog" and self.syslog_dbg is not None:
            return 200, self.syslog_dbg, "text/plain"
        return None

    @property
    def address(self):
        return "%s:%d" % (self.host, self.port)

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        if self._devs4028 is not None:
            self._devs4028_thread = threading.Thread(target=self._devs4028.serve_forever, daemon=True)
            self._devs4028_thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self._devs4028 is not None:
            self._devs4028.shutdown()
            self._devs4028.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    p = argparse.ArgumentParser(description="fake Goldshell Box miner serving the test fixtures")
    p.add_argument("--port", type=int, default=8999)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--password", default="password")
    a = p.parse_args(argv)
    fm = FakeMiner(a.bind, a.port, a.password).start()
    print("fake miner on http://%s  (password: %s)  Ctrl-C to stop" % (fm.address, a.password))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fm.stop()


if __name__ == "__main__":
    main()
