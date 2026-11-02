"""tools/hs_probe.py against the fake miner on 127.0.0.1 and nothing else.

The leak tests (redaction, withheld syslog) run on synthetic data only: there is no real capture to replay
because the tool is new. Run this module on its own: python -m unittest -v tests.test_hs_probe
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from gbox import config
from tests import fake_miner
from tests.fake_miner import FakeMiner
from tools import hs_probe

SECRETS = ("FAKEWALLETabc123xyz", "fakepass9", "FakeSSID-77", "fakewifikey55")
MAC_FORMS = ("02:1a:2b:3c:4d:5e",)
PASSWORD = "s3cretAdmin9"


class FakeClock:
    """A virtual clock: sleep advances it, requests take no virtual time, so a run takes real seconds."""

    def __init__(self, on_sleep=None):
        self.now = 1000.0
        self.sleeps = []
        self.on_sleep = on_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "gbox-data"            # stands in for GBOX_DATA; the real ~/.gbox is never read
        self.data.mkdir()
        self.out = self.tmp / "out"
        self.fm = FakeMiner(fixtures="hsbox_synthetic", port4028=True, study=True, password=PASSWORD)
        self.fm.start()
        self.addCleanup(self._stop)
        self._stopped = False

    def _stop(self):
        if not self._stopped:
            self._stopped = True
            self.fm.stop()

    def stop_fake(self):
        self._stop()

    def run_probe(self, *extra, password=PASSWORD, clock=None, host=None, out=None, label="t", environ=None):
        clock = clock or FakeClock()
        env = {"GBOX_DATA": str(self.data)} if environ is None else dict(environ)
        if password is not None:
            env[hs_probe.PASSWORD_ENV] = password
        argv = ["--label", label, "--host", host or self.fm.address, "--port4028", str(self.fm.devs4028_port),
                "--out", str(out or self.out)] + list(extra)
        so, se = io.StringIO(), io.StringIO()
        rc = hs_probe.main(argv, environ=env, isatty=lambda: False, clock=clock, sleep=clock.sleep,
                           stdout=so, stderr=se)
        return rc, so.getvalue(), se.getvalue(), clock

    def run_dir(self, label="t", out=None):
        dirs = list(((out or self.out) / label).iterdir())
        self.assertEqual(len(dirs), 1)
        return dirs[0]

    def manifest(self, label="t"):
        with open(self.run_dir(label) / "manifest.json", encoding="utf-8") as f:
            return json.load(f)["requests"]

    def tree_text(self):
        parts = []
        for p in self.out.rglob("*"):
            if p.is_file():
                parts.append(p.read_text(encoding="utf-8"))
        return "\n".join(parts)


class FullRunTest(ProbeCase):
    def test_1_full_run_makes_the_21_requests_in_order_paced_and_alone(self):
        rc, out, err, clock = self.run_probe()
        self.assertEqual(rc, 0, err)
        log = self.fm.request_log
        expected = [("4028" if r[1] == "4028" else "http", r[2]) for r in hs_probe.ALLOWLIST]
        self.assertEqual(len(expected), 21)
        self.assertEqual([(e["kind"], e["target"]) for e in log], expected)
        self.assertEqual({e["method"] for e in log if e["kind"] == "http"}, {"GET"})
        self.assertEqual(self.fm.restarts, 0)
        ordered = sorted(log, key=lambda e: e["start"])
        for a, b in zip(ordered, ordered[1:]):
            self.assertLessEqual(a["end"], b["start"], "two requests overlapped")
        self.assertEqual(self.fm.max_in_flight, 1)
        # 20 gaps, each one the full interval from the previous start (requests take no virtual time)
        self.assertEqual(clock.sleeps, [10.0] * 20)
        self.assertEqual(len(self.manifest()), 21)
        self.assertEqual(sum(1 for line in out.splitlines() if re.match(r"\d\d  ", line)), 21)

    def test_slow_requests_shorten_the_wait_but_never_the_gap(self):
        """Start-to-start, not end-to-start: a 4 s request is followed by a 6 s wait."""
        clock = FakeClock()
        real_raw = hs_probe.Sender.raw4028

        def slow(self_, command):
            clock.now += 4
            return real_raw(self_, command)

        hs_probe.Sender.raw4028 = slow
        try:
            self.run_probe("--only", "4028-version,4028-summary", clock=clock)
        finally:
            hs_probe.Sender.raw4028 = real_raw
        self.assertEqual(clock.sleeps, [6.0])

    def test_4_nothing_secret_reaches_disk_stdout_or_stderr(self):
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        tree = self.tree_text()
        for needle in SECRETS:
            self.assertNotIn(needle, tree)
            self.assertNotIn(needle, out + err)
        flat = tree.lower().replace("-", ":")
        for mac in MAC_FORMS:
            self.assertNotIn(mac, flat)
            self.assertNotIn(mac, (out + err).lower().replace("-", ":"))
        for secret in (fake_miner.TOKEN, PASSWORD, self.fm.password_hex):
            self.assertNotIn(secret, tree)
            self.assertNotIn(secret, out + err)
        # the check above would pass on an empty tree, so show the redaction did happen
        text = (self.run_dir() / "05-4028-pools.json").read_text(encoding="utf-8")
        self.assertIn("<redacted:pool-user>", text)
        self.assertIn("<redacted:pool-user>", (self.run_dir() / "18-minersyslog.txt").read_text(encoding="utf-8"))
        self.assertIn("<redacted:mac>", (self.run_dir() / "14-minerinfo.txt").read_text(encoding="utf-8"))
        self.assertIn("<redacted:mac>", (self.run_dir() / "17-syslog.txt").read_text(encoding="utf-8"))
        self.assertIn("<redacted:ssid>", (self.run_dir() / "11-wifisetting.json").read_text(encoding="utf-8"))
        self.assertIn("<redacted:wifi-key>", (self.run_dir() / "11-wifisetting.json").read_text(encoding="utf-8"))
        pools = (self.run_dir() / "10-pools.json").read_text(encoding="utf-8")
        self.assertIn("<redacted:pool-pass>", pools)
        self.assertNotIn('"x"', pools)       # the one-character user and password are replaced as field values

    def test_fix3_secrets_echoed_into_the_syslog_are_scrubbed_on_every_capture(self):
        """The firmware echoes write bodies into /dbg/syslog; request 17 is not a secret source."""
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        tree = self.tree_text()
        for needle in ("oldwifipw", "OldNet", "OLDWALLETzz", "escpw12", "escuser"):
            self.assertNotIn(needle, tree)
            self.assertNotIn(needle, out + err)
        syslog = (self.run_dir() / "17-syslog.txt").read_text(encoding="utf-8")
        self.assertIn('{"password":"<redacted:secret>","ssid":"<redacted:ssid>"}', syslog)
        self.assertIn('"pass":"<redacted:secret>"', syslog)
        self.assertNotIn('"pass":"x"', syslog)
        self.assertIn('\\"username\\":\\"<redacted:pool-user>\\"', syslog)   # JSON escaped inside a log line

    def test_requests_before_the_secret_is_learned_are_redacted_too(self):
        """The pool user is learned at request 5; the captures from 1-4 are written after, with it known."""
        self.fm.fail_pools = False
        self.run_probe()
        for p in self.run_dir().iterdir():
            self.assertNotIn("FAKEWALLETabc123xyz", p.read_text(encoding="utf-8"))

    def test_stats_is_saved_as_text_because_it_is_malformed(self):
        self.run_probe()
        names = sorted(p.name for p in self.run_dir().iterdir())
        self.assertIn("04-4028-stats.txt", names)
        self.assertIn("03-4028-devs.json", names)
        self.assertIn("manifest.json", names)
        self.assertNotIn("07-login.json", names)
        entry = {e["name"]: e for e in self.manifest()}
        self.assertEqual(entry["login"]["status"], "ok")
        self.assertEqual(entry["login"]["saved"], "withheld")
        self.assertEqual(entry["cgminer"]["status"], 500)
        self.assertEqual(entry["defend"]["status"], 404)
        self.assertGreater(entry["4028-pools"]["redactions"], 0)
        self.assertEqual(entry["4028-version"]["bytes"],
                         len((Path(fake_miner.FIX) / "hsbox_synthetic" / "api4028_version.json")
                             .read_text(encoding="utf-8").rstrip("\n").encode("utf-8")) + 1)

    def test_only_runs_a_subset_in_table_order_and_adds_the_login_it_needs(self):
        self.run_probe("--only", "setting,4028-version")
        self.assertEqual([(e["kind"], e["target"]) for e in self.fm.request_log],
                         [("4028", "version"), ("http", "/user/login"), ("http", "/mcb/setting")])


class SenderTest(ProbeCase):
    def test_2_interval_under_ten_is_rejected(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.run_probe("--interval", "9")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("at least 10", err.getvalue())
        self.assertEqual(self.fm.request_log, [])
        with self.assertRaises(ValueError):
            hs_probe.Probe(hs_probe.Sender(self.fm.address), interval=9)

    def test_fix1_non_finite_intervals_are_rejected(self):
        for bad in ("nan", "inf", "-inf", "NaN"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                self.run_probe("--interval", bad)
            self.assertEqual(cm.exception.code, 2, bad)
        self.assertEqual(self.fm.request_log, [])
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                hs_probe.Probe(hs_probe.Sender(self.fm.address), interval=bad)

    def test_2_the_sender_refuses_anything_off_the_allowlist_before_a_socket_opens(self):
        s = hs_probe.Sender(self.fm.address, self.fm.devs4028_port)
        method = "PU" + "T"                       # spelled so the source check below stays honest
        with self.assertRaises(hs_probe.NotAllowed):
            s.http(method, "/mcb/setting", fake_miner.TOKEN)
        with self.assertRaises(hs_probe.NotAllowed):
            s.http("GET", "/mcb/fac" + "rst", fake_miner.TOKEN)
        with self.assertRaises(hs_probe.NotAllowed):
            s.http("GET", "/mcb/status", None, {"x": "y"})       # only the login takes a query
        with self.assertRaises(hs_probe.NotAllowed):
            s.raw4028("restart")
        self.assertEqual(self.fm.request_log, [])

    def test_3_source_has_no_write_method_and_no_factory_reset_path(self):
        src = (Path(hs_probe.__file__)).read_text(encoding="utf-8")
        for bad in ('"PU' + 'T"', "'PU" + "T'", '"PO' + 'ST"', "'PO" + "ST'", "fac" + "rst"):
            self.assertNotIn(bad, src)


class BehaviorTest(ProbeCase):
    def test_5_both_pools_reads_failing_withholds_the_miner_log(self):
        self.fm.fail_pools = True
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        entry = {e["name"]: e for e in self.manifest()}
        self.assertEqual(entry["minersyslog"]["saved"], "withheld")
        self.assertIn("minersyslog", [e["target"].split("/")[-1] for e in self.fm.request_log if e["kind"] == "http"],
                      "the request is still made")
        self.assertEqual(entry["minersyslog"]["status"], 200)
        self.assertEqual(entry["pools"]["status"], 500)
        self.assertEqual([p for p in self.run_dir().iterdir() if "minersyslog" in p.name], [])
        self.assertNotIn("FAKEWALLETabc123xyz", self.tree_text())

    def test_6_a_401_is_recorded_once_and_not_retried(self):
        self.fm.unauthorized_paths = {"/mcb/setting"}
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        self.assertEqual([e["target"] for e in self.fm.request_log].count("/mcb/setting"), 1)
        self.assertEqual(self.fm.logins, 1)                      # no second login
        entry = {e["name"]: e for e in self.manifest()}
        self.assertEqual(entry["setting"]["status"], 401)
        self.assertEqual(entry["hshistory"]["status"], 200)       # the run went on
        self.assertEqual(len(self.manifest()), 21)

    def test_7_three_connection_failures_in_a_row_abort_and_the_manifest_is_still_written(self):
        clock = FakeClock(on_sleep=lambda n: self.stop_fake() if n == 9 else None)   # before request 10
        rc, out, err, _ = self.run_probe(clock=clock)
        self.assertEqual(rc, 1)
        m = self.manifest()
        self.assertEqual(len(m), 21)
        self.assertEqual([e["status"] for e in m[:9]], ["ok"] * 5 + [200, "ok", 200, 200])
        self.assertEqual([e["status"] for e in m[9:12]], ["error"] * 3)
        self.assertEqual({e["status"] for e in m[12:]}, {"aborted"})
        self.assertEqual(len(clock.sleeps), 11)                   # no more waits once it gave up
        self.assertIn("aborted", err)

    def test_8_without_a_password_only_the_tokenless_requests_run(self):
        rc, out, err, clock = self.run_probe(password=None)
        self.assertEqual(rc, 0, err)
        log = self.fm.request_log
        self.assertEqual([(e["kind"], e["target"]) for e in log],
                         [("4028", "version"), ("4028", "summary"), ("4028", "devs"), ("4028", "stats"),
                          ("4028", "pools"), ("http", "/mcb/status")])
        self.assertFalse(any(e["auth"] for e in log))
        self.assertEqual(self.fm.logins, 0)
        m = self.manifest()
        self.assertEqual([e["status"] for e in m[6:]], ["skipped"] * 15)
        self.assertEqual({e["saved"] for e in m[6:]}, {"skipped"})
        self.assertEqual(len(m), 21)

    def test_a_wrong_password_stops_after_the_login_and_sends_no_token_request(self):
        rc, out, err, _ = self.run_probe(password="not-it")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.fm.logins, 1)
        m = {e["name"]: e for e in self.manifest()}
        self.assertEqual(m["login"]["status"], "fail")
        self.assertEqual(m["status"]["status"], "skipped")
        self.assertEqual(m["status"]["note"], "login failed")
        self.assertFalse(any(e["auth"] for e in self.fm.request_log))

    def test_9_dry_run_makes_no_contact_and_creates_nothing(self):
        rc, out, err, clock = self.run_probe("--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.fm.request_log, [])
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(sum(1 for line in out.splitlines() if re.match(r"\d\d  ", line)), 21)
        self.assertIn("output:", out)
        self.assertFalse(self.out.exists())

    def test_10_the_watched_host_is_refused_before_any_contact(self):
        config.save(config.Config(host="127.0.0.1:1234"), self.data)
        rc, out, err, clock = self.run_probe()
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])
        self.assertFalse(self.out.exists())
        self.assertIn("watches", err)
        rc, out, err, clock = self.run_probe("--allow-watched-host", "--only", "4028-version")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.fm.request_log), 1)

    def test_10_an_output_directory_inside_a_git_work_tree_is_refused(self):
        repo = self.tmp / "somerepo"
        (repo / ".git").mkdir(parents=True)
        rc, out, err, _ = self.run_probe(out=repo / "deep" / "study")
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])
        self.assertFalse((repo / "deep").exists())
        self.assertIn("git work tree", err)

    def test_an_unreadable_gbox_config_is_a_refusal_not_a_guess(self):
        (self.data / "config.json").write_text("{not json", encoding="utf-8")
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])


class RedactorTest(unittest.TestCase):
    def test_mac_forms_and_tokens(self):
        r = hs_probe.Redactor()
        text = "a 02:1A:2b:3C:4d:5E b 02-1a-2B-3c-4D-5e c Bearer abc.def.ghi d eyJhbGciOiJSUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.c2ln"
        out, n = r.apply(text)
        self.assertEqual(n, 4)
        self.assertNotRegex(out, r"(?i)5e|Bearer abc|eyJ")

    def test_fix2_wallet_before_the_worker_dot_is_learned_too(self):
        r = hs_probe.Redactor()
        r.learn(hs_probe.POOLS_HTTP, '[{"user":"WALLETabc123xyz.rig7","pass":"zz"}]')
        out, n = r.apply("payout to WALLETabc123xyz confirmed; worker rig7 up; WALLETabc123xyz.rig7")
        self.assertNotIn("WALLETabc123xyz", out)
        self.assertIn("worker rig7 up", out)         # the worker part alone is not a secret
        r2 = hs_probe.Redactor()
        r2.learn(hs_probe.POOLS_HTTP, '[{"user":"abc.w1","pass":"zz"}]')    # prefix under 4 characters: skipped
        self.assertEqual(r2.apply("abc here")[0], "abc here")

    def test_short_secrets_are_not_replaced_in_other_text_but_their_fields_are(self):
        r = hs_probe.Redactor()
        raw = '[{"user":"x","pass":"x"}]'
        r.learn(hs_probe.POOLS_HTTP, raw)
        out, n = r.apply(raw, hs_probe.POOLS_HTTP)
        self.assertEqual(out, '[{"user":"<redacted:pool-user>","pass":"<redacted:pool-pass>"}]')
        self.assertEqual(r.apply("x marks the spot")[0], "x marks the spot")
        self.assertFalse(r.has_pool_user)       # a user too short to find in other text counts as none


if __name__ == "__main__":
    unittest.main()
