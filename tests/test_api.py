"""The serialized session against the fake miner."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from gbox import api
from tests.fake_miner import FakeMiner, TOKEN


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        api.Miner.RETRY_DELAY = 0.01   # keep the retry path fast in tests

    def miner(self, **kw):
        kw.setdefault("password", "password")
        return api.Miner(self.fm.address, **kw)

    def test_login_and_typed_reads(self):
        m = self.miner()
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(m.minerinfo()["clock"], 600.0)
        self.assertEqual(len(m.boards()[0]), 16)
        self.assertEqual(len(m.history()), 288)
        self.assertEqual(self.fm.logins, 1)

    def test_wrong_password(self):
        with self.assertRaises(api.AuthError):
            self.miner(password="nope").status()

    def test_password_hex_and_token_are_alternatives(self):
        self.assertEqual(self.miner(password=None, password_hex=self.fm.password_hex).status()["firmware"], "2.2.5")
        self.assertEqual(self.miner(password=None, token=TOKEN).status()["firmware"], "2.2.5")
        self.assertEqual(self.fm.logins, 1)

    def test_no_credentials(self):
        m = api.Miner(self.fm.address)
        self.assertFalse(m.has_credentials)
        with self.assertRaises(api.NoCredentials):
            m.status()

    def test_bad_token_rejected_locally(self):
        with self.assertRaises(ValueError):
            api.Miner(self.fm.address).set_token("not a jwt")

    def test_single_401_is_retried_not_believed(self):
        m = self.miner()
        m.status()
        self.fm.unauthorized_next = 1
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(m.unauthorized_count, 1)
        self.assertEqual(self.fm.logins, 1)

    def test_persistent_401_relogs_in_once_then_fails(self):
        m = self.miner()
        m.status()
        self.fm.unauthorized_next = 3           # exhausts the retries, then re-login succeeds
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(self.fm.logins, 2)
        self.fm.unauthorized_next = 100         # nothing helps
        with self.assertRaises(api.AuthError):
            m.status()
        self.assertFalse(m.has_token)

    def test_token_only_session_cannot_relogin(self):
        m = self.miner(password=None, token=TOKEN)
        self.fm.unauthorized_next = 3
        with self.assertRaises(api.AuthError):
            m.status()
        self.assertEqual(self.fm.logins, 0)

    def test_requests_are_serialized(self):
        self.fm.delay = 0.05
        m = self.miner()
        m.status()
        errors = []

        def worker():
            try:
                for _ in range(3):
                    m.minerinfo()
            except Exception as e:      # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.fm.max_in_flight, 1)
        self.assertEqual(m.request_count, 1 + 12)

    def test_set_plan_keeps_volts_and_fans(self):
        m = self.miner()
        self.assertEqual(m.set_plan(625), "625 MHz 0.41 V 90 RPM 90 RPM")
        self.assertTrue(self.fm.setting["manual"])
        self.assertEqual(m.set_plan(600, volts=0.4), "600 MHz 0.4 V 90 RPM 90 RPM")

    def test_set_plan_validates(self):
        m = self.miner()
        for bad in (610, 275, 750):
            with self.assertRaises(ValueError):
                m.set_plan(bad)
        self.assertEqual(self.fm.setting["manualPowerplan"], "600 MHz 0.41 V 90 RPM 90 RPM")

    def test_fan_target_clamped_to_firmware_range(self):
        m = self.miner()
        self.assertEqual(m.set_fan_target(70), 70)
        with self.assertRaises(ValueError):
            m.set_fan_target(60)

    def test_restart(self):
        self.miner().restart()
        self.assertEqual(self.fm.restarts, 1)

    def test_unreachable_is_miner_error_without_url(self):
        m = api.Miner("127.0.0.1:1", password="password", timeout=1)
        with self.assertRaises(api.MinerError) as cm:
            m.status()
        self.assertNotIn("http://", str(cm.exception))
        self.assertNotIn(self.fm.password_hex, str(cm.exception))


class ChipTempsTest(unittest.TestCase):
    """The cgminer log's Avgtemp/MaxTemp lines become numbers and timestamps; nothing else leaves the parser."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "dbg_minersyslog.txt"), encoding="utf-8") as f:
            self.text = f.read()

    def readings(self, text, cursor=None, first_minutes=None):
        return api.parse_chiptemps(text, cursor=cursor, first_minutes=first_minutes)[0]

    def test_every_temperature_line_is_a_tuple_in_log_order(self):
        rows = self.readings(self.text)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], ("2026-09-15 07:36:01", 54.0, 65.0))
        self.assertEqual(rows[-1], ("2026-09-15 07:36:31", 70.0, 82.0))
        self.assertEqual(max(r[2] for r in rows), 93.0)

    def test_a_cursor_keeps_only_lines_after_it_in_the_log(self):
        lines = self.text.splitlines(keepends=True)
        cut = next(i for i, line in enumerate(lines) if "07:36:16" in line) + 1
        _, cursor = api.parse_chiptemps("".join(lines[:cut]))
        rows, cursor = api.parse_chiptemps(self.text, cursor=cursor)
        self.assertEqual([r[0] for r in rows], ["2026-09-15 07:36:21", "2026-09-15 07:36:26", "2026-09-15 07:36:31"])
        self.assertEqual(api.parse_chiptemps(self.text, cursor=cursor)[0], [])

    def test_the_cursor_holds_nothing_of_the_logs_text(self):
        _, cursor = api.parse_chiptemps(self.text + " [2026-09-15 08:00:00] Pool 0 example.worker1 alive\n")
        self.assertIsInstance(cursor, api.LogCursor)
        self.assertNotIn("example.worker1", repr(cursor))
        self.assertEqual(cursor.stamp, "2026-09-15 08:00:00")

    def test_output_holds_numbers_and_timestamps_only(self):
        # the log repeats the pool user; the parser's output must never be able to carry it
        self.assertIn("example.worker1", self.text)
        for ts, avg, mx in self.readings(self.text):
            self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
            self.assertIsInstance(avg, float)
            self.assertIsInstance(mx, float)

    def test_garbage_and_empty_text_parse_to_nothing(self):
        self.assertEqual(api.parse_chiptemps(""), ([], None))
        self.assertEqual(self.readings("Chip Avgtemp nope'C, MaxTemp 'C\n[bad] C0: Chip Avgtemp 1'C"), [])

    def test_readings_before_the_newest_boot_line_belong_to_the_run_before(self):
        two = self.text + " [2026-09-15 08:00:00] C0: SCBOX Init sucessed. 16 chips, 256 Total goodcores. Wait 5s!!!\n"
        self.assertEqual(self.readings(two), [])
        two += " [2026-09-15 08:00:05] C0: Chip Avgtemp 30.000000'C, MaxTemp 38.000000'C\n"
        self.assertEqual(self.readings(two), [("2026-09-15 08:00:05", 30.0, 38.0)])

    def test_a_run_start_phrase_inside_another_line_is_not_a_run_start(self):
        # review of 0.9.1: the log repeats the pool user, which the owner chooses; it must not move the run start
        echoed = self.text + " [2026-09-15 08:00:00] Pool 0 stratum+tcp://example.invalid user Started intminer \n"
        self.assertEqual(self.readings(echoed), self.readings(self.text))

    def test_a_boot_with_no_time_yet_is_placed_by_its_position_in_the_log(self):
        # 0.9.1, 2026-09-21 09:44: a cold boot's clock reads 2007 until it reaches a time server. Compared by
        # timestamp, the run before the outage looked newer than the boot, and its 79 C peak landed on the first
        # row of a miner one second into its uptime.
        old = (" [2026-09-21 19:20:00] C0: Chip Avgtemp 64.000000'C, MaxTemp 79.000000'C\n"
               " [2026-09-21 19:27:20] C0: Chip Avgtemp 64.000000'C, MaxTemp 78.000000'C\n")
        boot = " [2007-01-01 08:03:18] Started intminer 5.4.2-unknown\n"
        _, at_old = api.parse_chiptemps(old)
        self.assertEqual(self.readings(old + boot), [])
        rows, at_boot = api.parse_chiptemps(old + boot, cursor=at_old)
        self.assertEqual(rows, [])
        early = " [2007-01-01 08:03:30] C0: Chip Avgtemp 25.000000'C, MaxTemp 30.000000'C\n"
        later = " [2026-09-21 21:44:40] C0: Chip Avgtemp 30.000000'C, MaxTemp 38.000000'C\n"
        _, at_early = api.parse_chiptemps(old + boot + early, cursor=at_boot)
        self.assertEqual([r[0] for r in self.readings(old + boot + early + later, cursor=at_early)],
                         ["2026-09-21 21:44:40"])
        self.assertEqual(self.readings(old + boot + early + later, first_minutes=5),
                         [("2026-09-21 21:44:40", 30.0, 38.0)])       # a first read stops at the jump

    def test_miner_syslog_is_one_get_of_text(self):
        fm = FakeMiner().start()
        self.addCleanup(fm.stop)
        m = api.Miner(fm.address, password="password")
        text = m.syslog()
        self.assertIn("MaxTemp", text)
        self.assertEqual(fm.requests[-1], ("GET", "/dbg/minersyslog"))
        self.assertGreaterEqual(len(api.parse_chiptemps(text)), 1)


if __name__ == "__main__":
    unittest.main()


class VerifyPasswordTest(unittest.TestCase):
    """The service checks a password the page offers for a power action, without touching its own session."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)

    def test_right_hex_is_true_and_the_session_is_untouched(self):
        m = api.Miner(self.fm.address, token=TOKEN)
        self.assertTrue(m.verify_password_hex(self.fm.password_hex))
        self.assertEqual(m.status()["firmware"], "2.2.5")        # still on its own token
        self.assertEqual(self.fm.logins, 1)                       # one login: the check

    def test_wrong_hex_is_auth_error(self):
        m = api.Miner(self.fm.address, token=TOKEN)
        with self.assertRaises(api.AuthError):
            m.verify_password_hex("00" * 16)
        self.assertEqual(m.status()["firmware"], "2.2.5")

    def test_bad_input_is_auth_error_without_a_request(self):
        m = api.Miner(self.fm.address, token=TOKEN)
        for bad in ("", None, "zz", 12, "0123"):
            with self.assertRaises(api.AuthError):
                m.verify_password_hex(bad)
        self.assertEqual(self.fm.logins, 0)

    def test_dead_miner_is_miner_error(self):
        m = api.Miner("127.0.0.1:1", token=TOKEN, timeout=1)
        with self.assertRaises(api.MinerError):
            m.verify_password_hex(self.fm.password_hex)


class FakeFidelityTest(unittest.TestCase):
    """Where the real firmware was read from a unit, the fake answers the same way."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)

    def get(self, path, token=None):
        req = urllib.request.Request("http://%s%s" % (self.fm.address, path))
        if token:
            req.add_header("Authorization", "Bearer " + token)
        return urllib.request.urlopen(req, timeout=5)

    def test_status_answers_a_request_that_carries_no_token(self):
        # Verified on the SC-BOX 2026-09-19 with one curl between two polls:
        # GET /mcb/status returns 200 and the model string with no Authorization
        # header. Unverified on the SC5 Pro II. This is what `gbox discover` probes.
        with self.get("/mcb/status") as r:
            self.assertEqual(r.status, 200)
            body = json.loads(r.read().decode())
        self.assertEqual(body["model"], "Goldshell-SCBox")
        self.assertEqual(self.fm.logins, 0)

    def test_the_other_paths_still_refuse_a_request_with_no_token(self):
        for path in ("/mcb/setting", "/dbg/minerinfo", "/cpb/hshistory"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 401, path)

    def test_status_still_refuses_a_wrong_token(self):
        # Unverified against the firmware; kept strict so nothing is invented,
        # and discover never sends a token at all.
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/mcb/status", token=TOKEN[:-1] + "x")
        self.assertEqual(caught.exception.code, 401)
