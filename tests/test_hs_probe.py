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
        self.home = self.tmp / "home"
        (self.home / ".gbox").mkdir(parents=True)
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
        # HOME and USERPROFILE point at a temp dir so the real ~/.gbox/config.json is never read
        env = ({"GBOX_DATA": str(self.data), "HOME": str(self.home), "USERPROFILE": str(self.home)}
               if environ is None else dict(environ))
        if password is not None:
            env[hs_probe.PASSWORD_ENV] = password
        if host is None:
            ip, port = self.fm.address.rsplit(":", 1)
            hostargs = ["--host", ip, "--http-port", port]
        else:
            hostargs = ["--host", host]
        argv = ["--label", label] + hostargs + ["--port4028", str(self.fm.devs4028_port),
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
        self.assertIn("<line withheld:", (self.run_dir() / "18-minersyslog.txt").read_text(encoding="utf-8"))
        self.assertIn("<redacted:mac>", (self.run_dir() / "14-minerinfo.txt").read_text(encoding="utf-8"))
        self.assertIn("<line withheld:", (self.run_dir() / "17-syslog.txt").read_text(encoding="utf-8"))
        self.assertIn("<redacted:ssid>", (self.run_dir() / "11-wifisetting.json").read_text(encoding="utf-8"))
        self.assertIn("<redacted:wifi-key>", (self.run_dir() / "11-wifisetting.json").read_text(encoding="utf-8"))
        pools = (self.run_dir() / "10-pools.json").read_text(encoding="utf-8")
        self.assertIn("<redacted:pool-pass>", pools)
        self.assertNotIn('"x"', pools)       # the one-character user and password are replaced as field values

    def test_fix3_secrets_echoed_into_the_syslog_are_withheld_by_line(self):
        """The firmware echoes write bodies into /dbg/syslog; those lines are dropped whole."""
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        tree = self.tree_text()
        for needle in ("oldwifipw", "OldNet", "OLDWALLETzz", "escpw12", "escuser"):
            self.assertNotIn(needle, tree)
            self.assertNotIn(needle, out + err)
        syslog = (self.run_dir() / "17-syslog.txt").read_text(encoding="utf-8")
        self.assertNotIn("<redacted:", syslog)           # no field scrub on the logs
        self.assertIn("<line withheld: ", syslog)

    def test_linedrop_every_echo_shape_is_gone_from_disk(self):
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        tree = self.tree_text()
        for needle in ("sq_pw_1", "kv_pw_2", "KvNet", "wp_3", "pp_4", "de_5", "q9tail", "x9x9"):
            self.assertNotIn(needle, tree)
        self.assertNotIn("s3cretadmin9", tree.lower())            # also the uppercase form, on a line with no drop word
        self.assertNotIn("walletabc123", tree.lower())

    def test_linedrop_useful_lines_survive_unchanged(self):
        self.run_probe()
        syslog = (self.run_dir() / "17-syslog.txt").read_text(encoding="utf-8").splitlines()
        for line in (" [2026-11-01 07:36:02] Minerd start !!!", " [2026-11-01 07:36:03] Check Token Error",
                     " [2026-11-01 07:36:05] Restart Miner",
                     " [2026-11-01 07:36:06] chip temp 65 fan 3600 rpm clock 850"):
            self.assertIn(line, syslog)
        self.assertNotIn(" [2026-11-01 07:36:04] Check Token Error password=x9x9", syslog)
        self.assertIn("<line withheld: ", "\n".join(syslog))
        self.assertIn(" [2026-11-01 07:36:03] Restart Miner",
                      (self.run_dir() / "18-minersyslog.txt").read_text(encoding="utf-8").splitlines())
        status = (self.run_dir() / "06-status-notoken.json").read_text(encoding="utf-8")
        self.assertIn('"hashrate": 116321183393', status)
        self.assertIn('"ver": "a1b2c3d4e5f6"', status)

    def test_linedrop_a_key_temp_echo_does_not_blank_temp_elsewhere(self):
        self.run_probe()
        self.assertIn("temp0", (self.run_dir() / "15-icinfo.json").read_text(encoding="utf-8"))
        self.assertNotIn("<redacted:wifi-key>", (self.run_dir() / "15-icinfo.json").read_text(encoding="utf-8"))

    def test_linedrop_manifest_counts_lines(self):
        self.run_probe()
        entry = {e["name"]: e for e in self.manifest()}
        for name in ("syslog", "minersyslog"):
            e = entry[name]
            self.assertGreater(e["lines_total"], e["lines_withheld"])
            self.assertGreater(e["lines_withheld"], 0)
            self.assertEqual(e["redactions"], e["lines_withheld"])
        self.assertNotIn("lines_total", entry["status"])

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


class PlanTest(unittest.TestCase):
    def test_review3_a_log_selection_includes_the_learning_reads_in_order(self):
        for log in ("syslog", "minersyslog"):
            with self.subTest(only=log):
                nums = [n for n, _ in hs_probe.plan([log])]
                self.assertEqual([n for n in nums if n in (5, 7, 10, 11)], [5, 7, 10, 11])
                self.assertEqual(nums, sorted(nums))
                self.assertIn(hs_probe.NAMES.index(log) + 1, nums)


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

    def test_fix6_the_home_config_is_checked_even_when_gbox_data_is_set(self):
        config.save(config.Config(host="127.0.0.1:1234"), self.home / ".gbox")     # watched host lives in ~/.gbox
        self.assertFalse((self.data / "config.json").exists())                       # GBOX_DATA has none
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])
        self.assertIn("watches", err)
        # the other way round: a different host in each is fine, a match in either one refuses
        config.save(config.Config(host="10.9.9.9"), self.home / ".gbox")
        config.save(config.Config(host="127.0.0.1"), self.data)
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])
        config.save(config.Config(host="10.9.9.9"), self.data)
        rc, out, err, _ = self.run_probe("--only", "4028-version", label="ok")
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

    def test_review3_syslog_withheld_when_nothing_was_learned(self):
        """4028 pools fails and /mcb/pools answers 401: no pool user, so /dbg/syslog is withheld whole."""
        self.fm.fail_pools = True
        self.fm.unauthorized_paths = {"/mcb/pools"}
        self.fm.syslog_dbg = "[2026-11-01 07:35:49] stratum connect pool.example:3333 as bc1qxyz9rig.rig1\n"
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        entry = {e["name"]: e for e in self.manifest()}
        self.assertEqual(entry["syslog"]["saved"], "withheld")
        self.assertIn("pool user", entry["syslog"]["note"])
        self.assertEqual([p for p in self.run_dir().iterdir() if "syslog" in p.name], [])
        self.assertNotIn("bc1qxyz9rig", self.tree_text() + out + err)

    def test_review3_syslog_withheld_when_wifi_read_did_not_succeed(self):
        self.fm.unauthorized_paths = {"/mcb/wifisetting"}
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 0, err)
        entry = {e["name"]: e for e in self.manifest()}
        self.assertEqual(entry["syslog"]["saved"], "withheld")
        self.assertIn("wifi", entry["syslog"]["note"].lower())

    def test_review3_only_syslog_runs_the_learning_reads_first(self):
        self.run_probe("--only", "syslog")
        self.assertEqual([(e["kind"], e["target"]) for e in self.fm.request_log],
                         [("4028", "pools"), ("http", "/user/login"), ("http", "/mcb/pools"),
                          ("http", "/mcb/wifisetting"), ("http", "/dbg/syslog")])
        self.assertNotIn("FAKEWALLETabc123xyz", self.tree_text())

    BAD_HOSTS = ("1.2.3.4?a=", "1.2.3.4#", "1.2.3.4.", "001.002.003.004", "16909060",
                 "example.lan", "1.2.3.4:80", " 1.2.3.4")

    def test_review3_only_a_literal_ipv4_host_is_accepted(self):
        for bad in self.BAD_HOSTS:
            with self.subTest(host=bad):
                rc, out, err, _ = self.run_probe(host=bad)
                self.assertEqual(rc, 2)
                self.assertEqual(self.fm.request_log, [])
                self.assertFalse(self.out.exists())
                self.assertIn("IPv4", err)

    def test_review3_the_watched_host_guard_compares_addresses_not_strings(self):
        config.save(config.Config(host="192.168.8.201"), self.data)
        rc, out, err, _ = self.run_probe(host="192.168.8.201")
        self.assertEqual(rc, 2)
        self.assertIn("watches", err)
        self.assertEqual(self.fm.request_log, [])

    def test_review3_a_config_host_that_is_not_an_ip_refuses_to_run(self):
        config.save(config.Config(host="miner.lan"), self.data)
        rc, out, err, _ = self.run_probe()
        self.assertEqual(rc, 2)
        self.assertEqual(self.fm.request_log, [])
        self.assertFalse(self.out.exists())

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

    def test_fix4_the_admin_password_literal_is_scrubbed_unless_it_is_a_field_name(self):
        sender = hs_probe.Sender("127.0.0.1:1")
        p = hs_probe.Probe(sender, "s3cretAdmin9")
        self.assertEqual(p.redactor.apply("old s3cretAdmin9 gone")[0], "old <redacted:password> gone")
        for pw in ("password", "admin", "Password", "abc"):      # field names, and under 4 characters
            q = hs_probe.Probe(sender, pw)
            text = 'the password field and admin user "abc" %s' % '{"x":1}'
            self.assertEqual(q.redactor.apply(text)[0], text, pw)
        # the value of a field named password is still caught by the field scrub
        q = hs_probe.Probe(sender, "password")
        self.assertEqual(q.redactor.apply('{"password":"password"}')[0], '{"password":"<redacted:secret>"}')

    def test_fix5_other_mac_forms(self):
        r = hs_probe.Redactor()
        for text in ("mac=021a.2b3c.4d5e;", "mac 02 1a 2B 3c 4d 5E ok", "(02-1A-2B-3C-4D-5E)"):
            out, n = r.apply(text)
            self.assertNotRegex(out, r"(?i)021a|1a 2b|1a-2b|2b3c", text)
            self.assertIn("<redacted:mac>", out)
        keep = ("sha 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",   # inside a longer hex run
                "run 0123456789abcdef0123", "Total MH 116321183393.0423", "n=116321183393.5", "id 12345678901",
                "ver 1.2.3 build 2026 11 01", "hashrate 116321183393", "ver a1b2c3d4e5f6", "mac 021a2b3c4d5e up")
        for text in keep:
            self.assertEqual(r.apply(text)[0], text)

    def test_fix5_the_recorded_wifi_shape(self):
        raw = '{"network":[{"ssid":"NetA-77","password":"wifipw99"},{"ssid":"B","password":"zz"}]}'
        for source in (hs_probe.WIFI,):
            r = hs_probe.Redactor()
            r.learn(source, raw)
            out, n = r.apply(raw + " later: wifipw99 on NetA-77", source)
            self.assertNotIn("wifipw99", out)
            self.assertNotIn("NetA-77", out)
            self.assertNotIn('"zz"', out)
            self.assertNotIn('"B"', out)

    def test_short_secrets_are_not_replaced_in_other_text_but_their_fields_are(self):
        r = hs_probe.Redactor()
        raw = '[{"user":"x","pass":"x"}]'
        r.learn(hs_probe.POOLS_HTTP, raw)
        out, n = r.apply(raw, hs_probe.POOLS_HTTP)
        self.assertEqual(out, '[{"user":"<redacted:pool-user>","pass":"<redacted:pool-pass>"}]')
        self.assertEqual(r.apply("x marks the spot")[0], "x marks the spot")
        self.assertFalse(r.has_pool_user)       # a user too short to find in other text counts as none

    def test_linedrop_unit(self):
        r = hs_probe.Redactor()
        r.learn(hs_probe.POOLS_HTTP, '[{"user":"rigOwner77","pass":"zz"}]')
        text = ("ok line\nPayout to RIGOWNER77 done\r\nCheck Token Error\nCheck token error PWD=1\n"
                "mac 02:1a:2b:3c:4d:5e\nlast")
        out, total, withheld = r.drop_lines(text)
        self.assertEqual(total, 6)
        self.assertEqual(withheld, 3)
        lines = out.split("\n")
        self.assertEqual(lines[0], "ok line")
        self.assertEqual(lines[1], "<line withheld: 25 chars>\r")
        self.assertIn("Check Token Error\n", out)
        self.assertNotIn("PWD", out)
        self.assertIn("last", out)
        for word in hs_probe.LOG_DROP_WORDS:
            self.assertEqual(r.drop_lines("a " + word.upper() + " b")[2], 1, word)

    def test_literals_are_replaced_case_insensitively_and_only_pool_and_wifi_reads_teach(self):
        r = hs_probe.Redactor()
        r.learn(hs_probe.POOLS_HTTP, '[{"user":"rigOwner77","pass":"zz"}]')
        self.assertEqual(r.apply("x RIGOWNER77 y")[0], "x <redacted:pool-user> y")
        r2 = hs_probe.Redactor()
        r2.learn("syslog", '{"key":"temp","user":"someoneelse","password":"leaked1"}')
        r2.learn(None, '{"ssid":"OtherNet"}')
        self.assertEqual(r2.apply("temp someoneelse leaked1 OtherNet")[0], "temp someoneelse leaked1 OtherNet")


if __name__ == "__main__":
    unittest.main()
