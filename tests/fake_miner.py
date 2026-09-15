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
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gbox import aes

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.ZmFrZS1zaWduYXR1cmU"


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class FakeMiner:
    def __init__(self, host="127.0.0.1", port=0, password="password", delay=0.0):
        self.password_hex = aes.encrypt_password(password)
        self.delay = delay                      # seconds each request takes; makes overlaps visible
        self.setting = json.loads(_read("mcb_setting.json"))
        self.status = json.loads(_read("mcb_status.json"))
        self.minerinfo = _read("dbg_minerinfo.txt")
        self.icinfo = _read("dbg_icinfo.json")
        self.history = _read("cpb_hshistory.json")
        self.syslog = _read("dbg_minersyslog.txt")     # the cgminer log: Avgtemp/MaxTemp lines, a boot line, a pool-user line
        self.restarts = 0
        self.logins = 0
        self.requests = []                      # (method, path) in arrival order
        self.unauthorized_next = 0              # answer 401 to this many upcoming authed requests
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        outer = self

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
                        return self._reply(200, routes[url.path](), "text/plain" if url.path.startswith("/dbg") else "application/json")
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
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

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
