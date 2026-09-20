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
        try:
            self.request.recv(4096)
        except OSError:
            pass
        self.request.sendall(self.server.payload)


class _Devs4028Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeMiner:
    def __init__(self, host="127.0.0.1", port=0, password="password", delay=0.0,
                 fixtures=None, port4028=False, dbg_locked_icinfo=False):
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
                with outer._lock:
                    outer.in_flight += 1
                    outer.max_in_flight = max(outer.max_in_flight, outer.in_flight)
                    outer.requests.append((self.command, self.path.split("?")[0]))
                    outer.seen_headers.append((self.command, self.path.split("?")[0], self.headers.get("Authorization")))
                if outer.delay:
                    time.sleep(outer.delay)

            def _leave(self):
                with outer._lock:
                    outer.in_flight -= 1

            def _authed(self):
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
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
