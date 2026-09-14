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

    def test_trials_table_follows_the_log(self):
        status, headers, body = self.get("/api/trials")
        t = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual((t["rollup"], t["segments"]), ([], []))
        self.miner.set_token(TOKEN)
        self.poller.poll_once()
        t = json.loads(self.get("/api/trials")[2])
        self.assertEqual(len(t["segments"]), 1)
        self.assertEqual(t["segments"][0]["clock"], 600)
        self.assertTrue(t["segments"][0]["short"])
        self.assertEqual(t["rollup"], [])
        st = (self.data / "log.csv").stat()
        self.assertEqual(self.state.trials_cache[0], (st.st_mtime_ns, st.st_size))
        cached = self.state.trials_cache[1]
        self.get("/api/trials")
        self.assertIs(self.state.trials_cache[1], cached)          # unchanged log: served from cache
        self.poller.poll_once()
        self.get("/api/trials")
        self.assertIsNot(self.state.trials_cache[1], cached)       # log grew: recomputed

    def test_trial_progress_file_is_served_when_present(self):
        self.assertEqual(json.loads(self.get("/api/trial")[2]), {})
        (self.data / "trial.json").write_text(json.dumps({"step": 2, "clock": 575}), encoding="utf-8")
        status, headers, body = self.get("/api/trial")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), {"step": 2, "clock": 575})
        (self.data / "trial.json").write_text("{not json", encoding="utf-8")   # half-written by the runner
        self.assertEqual(json.loads(self.get("/api/trial")[2]), {})

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

    def test_csv_tail_keeps_the_header_and_the_last_rows(self):
        self.miner.set_token(TOKEN)
        for _ in range(4):
            self.poller.poll_once()
        full = self.get("/api/log.csv")[2].decode("utf-8").splitlines()
        self.assertEqual(len(full), 5)
        tail = self.get("/api/log.csv?tail=2")[2].decode("utf-8").splitlines()
        self.assertEqual(tail[0], full[0])
        self.assertEqual(tail[1:], full[-2:])
        self.assertEqual(len(self.get("/api/log.csv?tail=999")[2].decode("utf-8").splitlines()), 5)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/log.csv?tail=x")
        self.assertEqual(cm.exception.code, 400)

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

    def test_dashboard_event_keeps_500_characters(self):
        """A hand-written note with meter readings ran past the old 200-character cap twice on 2026-09-12."""
        msg = "x" * 499 + "END" + "y" * 100
        self.assertEqual(self.post("/api/event", json.dumps({"message": msg}).encode()), 204)
        text = self.events.tail()[0].split("dashboard: ", 1)[1].rstrip("\n")
        self.assertEqual(len(text), 500)
        self.assertTrue(text.endswith("E"))

    def test_health_carries_the_ladder_timings_and_where_they_are_set(self):
        h = json.loads(self.get("/api/health")[2])
        lad = h["ladder"]
        self.assertEqual(lad["unreachable_minutes"], 2)
        self.assertEqual(lad["stall_minutes"], 5)
        self.assertEqual(lad["min_gap_minutes"], config.DEFAULT_WATCHDOG["min_gap_minutes"])
        self.assertEqual(lad["max_restarts_per_day"], config.DEFAULT_WATCHDOG["max_restarts_per_day"])
        self.assertIsNone(lad["after_minutes"])            # no plug configured on this state
        self.assertIsNone(lad["max_cycles_per_day"])
        self.assertEqual(lad["config_path"], str(self.data / "config.json"))
        self.state.cfg = config.Config(host=self.fm.address, port=0, power={"host": "p", "after_minutes": 7, "max_cycles_per_day": 4})
        lad = json.loads(self.get("/api/health")[2])["ladder"]
        self.assertEqual((lad["after_minutes"], lad["settle_minutes"], lad["max_cycles_per_day"]), (7, 20, 4))

    def test_dashboard_event_is_sanitized(self):
        msg = "a" * 500 + "\nservice: forged line\x07"
        self.assertEqual(self.post("/api/event", json.dumps({"message": msg}).encode()), 204)
        lines = self.events.tail()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("forged", lines[0].split("dashboard: ", 1)[1][:1])
        self.assertNotIn("\x07", lines[0])
        self.assertLess(len(lines[0]), 560)      # 500 + the stamp and prefix
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

    def test_health_power_block_when_no_plug(self):
        p = json.loads(self.get("/api/health")[2])["power"]
        self.assertFalse(p["configured"])
        self.assertIsNone(p["watts"])
        self.assertEqual(p["cycles_today"], 0)

    def test_health_power_block_from_the_poller_and_watchdog(self):
        from gbox.plug import KasaLegacy
        from gbox.watchdog import Watchdog
        from tests.fake_plug import FakePlug
        fake = FakePlug(watts=188.0).start()
        self.addCleanup(fake.stop)
        self.cfg.power = {"host": fake.address, "device_id": fake.device_id}
        plug = KasaLegacy(fake.address, timeout=1.0)
        self.state.watchdog = Watchdog(lambda: None, self.events, 30, plug=plug, power=self.cfg.power)
        self.state.poller = Poller(api.Miner(self.fm.address, password="password"), self.data / "log.csv", 30,
                                   plug=plug, events=self.events)
        self.state.poller.poll_once()
        h = json.loads(self.get("/api/health")[2])
        p = h["power"]
        self.assertTrue(p["configured"])
        self.assertEqual(p["model"], "HS110(US)")
        self.assertEqual(p["alias"], "fake plug")             # the plug's human-given name, for the Power tile
        self.assertEqual(p["state"], "on")
        self.assertEqual(p["watts"], 188.0)
        self.assertIs(p["cycle"], False)
        self.assertEqual(p["cycles_today"], 0)
        self.assertIsNone(p["last_reason"])
        self.assertEqual(h["model"], "Goldshell-SCBox")      # the miner's model, read once on the first good poll
        self.assertEqual(h["rated"]["rated_watts"], 200.0)
        self.assertEqual(h["rated"]["name"], "SC-BOX")
        self.assertTrue(h["profile"]["known"])                # the capability profile the page and the sampler read
        self.assertEqual(h["profile"]["plan_dialect"], "box")

    def test_health_model_is_unknown_before_the_first_good_poll(self):
        h = json.loads(self.get("/api/health")[2])
        self.assertIsNone(h["model"])
        self.assertIsNone(h["rated"])
        self.assertIsNone(h["profile"])

    def test_health_profile_for_a_model_not_in_the_table(self):
        self.fm.status = {"hardware": "x", "model": "Goldshell-KDBox", "mcbversion": "x", "firmware": "x"}
        self.state.poller = Poller(api.Miner(self.fm.address, password="password"), self.data / "log.csv", 30)
        self.state.poller.poll_once()
        h = json.loads(self.get("/api/health")[2])
        self.assertEqual(h["model"], "Goldshell-KDBox")
        self.assertIsNone(h["rated"])
        self.assertFalse(h["profile"]["known"])
        self.assertEqual(h["profile"]["board_source"], "icinfo")
        self.assertIsNone(h["power"]["alias"])


class SeriesRouteTest(ServerTest):
    """/api/series: the bucketed log for the charts, validated and cached by the log's modification time."""

    def series(self, query=""):
        status, headers, body = self.get("/api/series" + query)
        return status, json.loads(body)

    def test_default_shape_after_two_polls(self):
        self.miner.set_token(TOKEN)
        self.poller.poll_once(); self.poller.poll_once()
        status, s = self.series()
        self.assertEqual(status, 200)
        self.assertEqual((s["hours"], s["bucket_minutes"]), (24, 5))
        self.assertGreater(len(s["buckets"]), 200)
        last = [b for b in s["buckets"] if b["samples"]][-1]
        self.assertEqual(last["samples"], 2)
        self.assertIsNotNone(last["hashrate"])
        self.assertIn("worst", last)
        self.assertIsInstance(s["events"], list)

    def test_parameters_and_limits(self):
        status, s = self.series("?hours=72&bucket=30")
        self.assertEqual((status, s["hours"], s["bucket_minutes"]), (200, 72, 30))
        for bad in ("?hours=0", "?hours=999", "?bucket=1", "?bucket=x", "?hours=abc"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get("/api/series" + bad)
            self.assertEqual(cm.exception.code, 400, bad)

    def test_cached_until_the_log_changes(self):
        self.miner.set_token(TOKEN)
        self.poller.poll_once()
        a = self.state.series(24, 5)
        self.assertIs(self.state.series(24, 5), a)
        self.assertIsNot(self.state.series(72, 30), a)
        self.poller.poll_once()
        self.assertIsNot(self.state.series(24, 5), a)

    def test_events_come_from_the_event_log(self):
        self.events.write("watchdog: restart #1 sent (miner unreachable for 2 min)")
        status, s = self.series("?hours=1&bucket=5")
        self.assertEqual(len(s["events"]), 1)
        self.assertTrue(s["events"][0]["label"].startswith("watchdog:"))


class HoldAndPowerRoutesTest(ServerTest):
    """The owner's planned outages: /api/hold, /api/hold/release and /api/power."""

    def setUp(self):
        super().setUp()
        from gbox.watchdog import Watchdog
        self.wd = Watchdog(lambda: None, self.events, 30)
        self.state.watchdog = self.wd

    def post_json(self, path, obj=None):
        data = json.dumps(obj if obj is not None else {}).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, (json.loads(body) if body else None)

    def health(self):
        return json.loads(self.get("/api/health")[2])

    def with_plug(self, **kw):
        from gbox.plug import KasaLegacy
        from gbox.power import PowerControl
        from tests.fake_plug import FakePlug
        fake = FakePlug(watts=197.0, **kw).start()
        self.addCleanup(fake.stop)
        self.cfg.power = {"host": fake.address, "device_id": fake.device_id, "settle_minutes": 20, "off_seconds": 15}
        plug = KasaLegacy(fake.address, timeout=1.0)
        self.state.power_control = PowerControl(plug, self.cfg.power, self.wd, self.events, sleep=lambda s: None)
        return fake

    def test_hold_shows_in_health_and_release_clears_it(self):
        code, body = self.post_json("/api/hold", {"minutes": 30, "reason": "PSU swap"})
        self.assertEqual(code, 200)
        self.assertEqual(body["hold"]["minutes_left"], 30)
        self.assertEqual(body["hold"]["reason"], "PSU swap")
        self.assertEqual(self.health()["hold"]["source"], "page")
        code, body = self.post_json("/api/hold/release")
        self.assertEqual((code, body), (200, {"hold": None}))
        self.assertIsNone(self.health()["hold"])
        text = "".join(self.events.tail())
        self.assertIn("hold: started by you until", text)
        self.assertIn("hold: released by you", text)

    def test_hold_with_no_expiry_and_a_cleaned_reason(self):
        code, body = self.post_json("/api/hold", {"minutes": None, "reason": "line one\nline two\x07"})
        self.assertEqual(code, 200)
        self.assertIsNone(body["hold"]["until"])
        self.assertEqual(body["hold"]["reason"], "line one line two")

    def test_hold_validates(self):
        for bad in ({"minutes": 0}, {"minutes": 2000}, {"minutes": "x"}, {"minutes": 1.5}):
            self.assertEqual(self.post_json("/api/hold", bad)[0], 400, bad)
        self.assertIsNone(self.wd.hold)
        self.assertEqual(self.post_json("/api/hold", {})[0], 200)      # minutes absent: the default hour
        self.assertEqual(self.wd.hold_info()["minutes_left"], 60)

    def test_hold_without_a_watchdog_is_409(self):
        self.state.watchdog = None
        code, body = self.post_json("/api/hold", {"minutes": 30})
        self.assertEqual(code, 409)
        self.assertIn("watchdog", body["error"])
        self.assertEqual(self.post_json("/api/hold/release")[0], 409)

    def test_power_without_a_plug_is_409(self):
        code, body = self.post_json("/api/power", {"action": "on"})
        self.assertEqual(code, 409)
        self.assertIn("plug", body["error"])

    def test_off_with_the_right_password_opens_the_relay(self):
        fake = self.with_plug()
        code, body = self.post_json("/api/power", {"action": "off", "password_hex": self.fm.password_hex})
        self.assertEqual(code, 200, body)
        self.assertEqual(fake.relay, 0)
        self.assertIs(body["power"]["off_by_you"], True)
        self.assertIsNone(body["hold"]["until"])
        h = self.health()
        self.assertIs(h["power"]["off_by_you"], True)
        self.assertIsNone(h["hold"]["until"])
        self.assertIn("power: switched off by you (page; 197 W before)", "".join(self.events.tail()))

    def test_off_with_the_wrong_password_is_403_and_moves_nothing(self):
        fake = self.with_plug()
        code, body = self.post_json("/api/power", {"action": "off", "password_hex": "00" * 16})
        self.assertEqual(code, 403)
        self.assertIn("password", body["error"])
        self.assertEqual(fake.relay, 1)
        self.assertIsNone(self.wd.hold)
        self.assertEqual(self.post_json("/api/power", {"action": "off"})[0], 400)          # no password at all

    def test_off_when_the_miner_does_not_answer_is_409_naming_the_alternatives(self):
        fake = self.with_plug()
        self.state.miner = api.Miner("127.0.0.1:1", timeout=1)
        code, body = self.post_json("/api/power", {"action": "off", "password_hex": self.fm.password_hex})
        self.assertEqual(code, 409)
        self.assertIn("gbox power cycle", body["error"])
        self.assertEqual(fake.relay, 1)

    def test_on_needs_no_password(self):
        fake = self.with_plug(relay=0)
        code, body = self.post_json("/api/power", {"action": "on"})
        self.assertEqual(code, 200, body)
        self.assertEqual(fake.relay, 1)
        self.assertEqual(body["hold"]["minutes_left"], 20)
        self.assertIs(body["power"]["off_by_you"], False)

    def test_cycle_answers_at_once(self):
        fake = self.with_plug()
        import time
        t0 = time.monotonic()
        code, body = self.post_json("/api/power", {"action": "cycle", "password_hex": self.fm.password_hex, "off_seconds": 5})
        self.assertEqual(code, 200, body)
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(body["power"]["busy"], "cycling")
        self.state.power_control.join(5)
        self.assertEqual(fake.relay, 1)
        self.assertIn("power: cycled by you (page): off 5 s, on (197 W before)", "".join(self.events.tail()))

    def test_power_validates_the_action_and_off_seconds(self):
        self.with_plug()
        self.assertEqual(self.post_json("/api/power", {"action": "explode"})[0], 400)
        self.assertEqual(self.post_json("/api/power", {"action": "cycle", "password_hex": self.fm.password_hex, "off_seconds": 1})[0], 400)
        self.assertEqual(self.post_json("/api/power", {})[0], 400)

    def test_wrong_device_is_409(self):
        fake = self.with_plug()
        self.state.power_control.cfg["device_id"] = "other"      # the control keeps its own copy, as the watchdog does
        code, body = self.post_json("/api/power", {"action": "on"})
        self.assertEqual(code, 409)
        self.assertIn("gbox power init", body["error"])
        self.assertEqual(fake.relay, 1)


if __name__ == "__main__":
    unittest.main()
