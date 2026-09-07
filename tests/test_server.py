"""The local server: routes, token hand-off, bind address, no request logging."""
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gbox import api, config
from gbox.events import EventLog
from gbox.poller import Poller
from gbox.server import ServiceState, make_server
from tests.fake_miner import TOKEN, FakeMiner


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.cfg = config.Config(host=self.fm.address, port=0)
        self.miner = api.Miner(self.fm.address)             # no credentials: waits for the dashboard
        self.events = EventLog(self.data / "events.log")
        self.poller = Poller(self.miner, self.data / "log.csv", 30, events=self.events)
        self.state = ServiceState(self.cfg, self.miner, self.data, poller=self.poller, events=self.events)
        self.srv = make_server(self.state)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(self.url + path, data=body, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_binds_loopback_by_default(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_second_instance_on_same_port_fails(self):
        self.cfg.port = self.srv.server_address[1]
        with self.assertRaises(OSError):
            make_server(self.state)

    def test_static_pages(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Goldshell Box</title>", body)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(self.get("/app.js")[0], 200)
        self.assertIn("javascript", self.get("/app.js")[1]["Content-Type"])
        self.assertIn("text/css", self.get("/style.css")[1]["Content-Type"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_no_path_traversal(self):
        for p in ("/../pyproject.toml", "/web/../api.py", "/%2e%2e/aes.py"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(p)
            self.assertEqual(cm.exception.code, 404)

    def test_health_then_token_handoff(self):
        h = json.loads(self.get("/api/health")[2])
        self.assertFalse(h["has_token"])
        self.assertEqual(h["host"], self.fm.address)
        self.assertEqual(h["poll_interval"], 30)
        self.assertEqual(self.post("/api/token", json.dumps({"token": TOKEN}).encode()), 204)
        self.assertTrue(json.loads(self.get("/api/health")[2])["has_token"])
        # the poller can now sample without a password
        self.assertEqual(self.poller.poll_once()["http"], "ok")
        self.assertIn("token received", "".join(self.events.tail()))

    def test_token_endpoint_validates(self):
        self.assertEqual(self.post("/api/token", b"{\"token\": \"garbage\"}"), 400)
        self.assertEqual(self.post("/api/token", b"{}"), 400)
        self.assertEqual(self.post("/api/token", b"token=x", "application/x-www-form-urlencoded"), 415)
        self.assertEqual(self.post("/api/token", b"x" * 9000), 413)
        self.assertEqual(self.post("/api/other", b"{}"), 404)
        self.assertFalse(self.miner.has_token)

    def test_csv_and_events(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/log.csv")
        self.assertEqual(cm.exception.code, 404)
        self.miner.set_token(TOKEN)
        self.poller.poll_once()
        status, headers, body = self.get("/api/log.csv")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(body.startswith(b"time,http,"))
        self.assertEqual(json.loads(self.get("/api/latest")[2])["http"], "ok")
        self.events.write("hello")
        self.assertIn(b"hello", self.get("/api/events")[2])

    def test_requests_are_not_logged(self):
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            self.get("/api/health")
            self.post("/api/token", b"{}")
            with self.assertRaises(urllib.error.HTTPError):
                self.get("/missing?secret=1")
        finally:
            sys.stderr = old
        self.assertEqual(buf.getvalue(), "")

    def test_dashboard_event_is_logged_with_prefix(self):
        body = json.dumps({"message": "clock set to 625 MHz (plan \"625 MHz 0.41 V 90 RPM 90 RPM\")"}).encode()
        self.assertEqual(self.post("/api/event", body), 204)
        lines = "".join(self.events.tail())
        self.assertIn("dashboard: clock set to 625 MHz", lines)
        self.assertIn(b"dashboard: clock set", self.get("/api/events")[2])

    def test_dashboard_event_is_sanitized(self):
        msg = "a" * 500 + "\nservice: forged line\x07"
        self.assertEqual(self.post("/api/event", json.dumps({"message": msg}).encode()), 204)
        lines = self.events.tail()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("forged", lines[0].split("dashboard: ", 1)[1][:1])
        self.assertNotIn("\x07", lines[0])
        self.assertLess(len(lines[0]), 260)
        self.assertEqual(lines[0].count("\n"), 1)

    def test_dashboard_event_validates(self):
        self.assertEqual(self.post("/api/event", b"{}"), 400)
        self.assertEqual(self.post("/api/event", json.dumps({"message": "   "}).encode()), 400)
        self.assertEqual(self.post("/api/event", json.dumps({"message": 5}).encode()), 400)
        self.assertEqual(self.post("/api/event", b"m=x", "text/plain"), 415)
        self.assertEqual(self.events.tail(), [])

    def test_dashboard_restart_event_tells_the_watchdog(self):
        class WD:
            told = 0
            last_reason = None

            def restarts_today(self):
                return 0

            def external_restart(self):
                self.told += 1
        self.state.watchdog = WD()
        self.assertEqual(self.post("/api/event", json.dumps({"message": "soft restart sent", "restart": True}).encode()), 204)
        self.assertEqual(self.state.watchdog.told, 1)
        self.assertEqual(self.post("/api/event", json.dumps({"message": "fan target set to 70 C"}).encode()), 204)
        self.assertEqual(self.state.watchdog.told, 1)


if __name__ == "__main__":
    unittest.main()
