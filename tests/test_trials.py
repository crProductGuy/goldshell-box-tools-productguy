"""Clock trials: log.csv -> segments per clock and fan target -> table rows.

The fixture log is generated here, one row per minute, so every expected
number below is hand-computable from the generator's arithmetic.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from gbox import poller, trials

OLD_COLUMNS = ["time", "http", "elapsed", "mhs_av", "mhs_20s", "hwerr", "hwerr_pct", "accepted",
               "rejected", "clock", "fan0", "fan1", "tstemp0", "tstemp1", "tstemp2", "rebootcnt",
               "weak_chips"]
T0 = datetime(2026, 9, 8, 10, 0, 0)


def row(t, clock=600.0, elapsed=0, accepted=0, hwerr=0, hwerr_pct=0.4, rebootcnt=0, weak="",
        mhs=750000.0, temp=70.0, fan=3000, good=None, target=65, overheat=0):
    return {
        "time": t.strftime("%Y-%m-%d %H:%M:%S"), "http": "ok", "elapsed": elapsed, "mhs_av": mhs,
        "mhs_20s": mhs, "hwerr": hwerr, "hwerr_pct": hwerr_pct, "accepted": accepted, "rejected": 0,
        "clock": clock, "fan0": fan, "fan1": fan, "tstemp0": temp, "tstemp1": temp, "tstemp2": 63.0,
        "rebootcnt": rebootcnt, "weak_chips": weak,
        "nonces_good": good, "nonces_bad": hwerr, "temp_target": target, "overheat": overheat,
    }


def fixture_rows():
    """Five segments, in order: A, B (after a restart), C (after a gap), D (fan target change), E (short)."""
    rows = []
    # A: 600 MHz, 10:00-10:30, 31 rows. accepted +10/min, hwerr +1/min, chip 8 +8 good +2 bad per min.
    for i in range(31):
        rows.append(row(T0 + timedelta(minutes=i), elapsed=1000 + 60 * i, accepted=100 + 10 * i, hwerr=50 + i,
                        rebootcnt=5, weak="3:%d/40;8:%d/%d" % (800 + 10 * i, 500 + 8 * i, 50 + 2 * i),
                        good=10000 + 100 * i))
    # B: restart at 10:31 (elapsed and counters reset), 600 MHz, 10:31-10:55, 25 rows.
    # rebootcnt 0 -> 2 at i=12. chip 8 present for the first 10 rows only (it went clean).
    for i in range(25):
        weak = "8:%d/%d" % (100 + 8 * i, 10 + i) if i < 10 else ""
        rows.append(row(T0 + timedelta(minutes=31 + i), elapsed=4 + 60 * i, accepted=10 * i, hwerr=i,
                        rebootcnt=0 if i < 12 else 2, weak=weak, good=1000 + 100 * i, mhs=760000.0))
    # gap 10:55 -> 11:06 (11 min, service was down)
    # C: 575 MHz, 11:06-11:40, 35 rows, no weak chips. One row with blank numbers in the middle.
    for i in range(35):
        r = row(T0 + timedelta(minutes=66 + i), clock=575.0, elapsed=2200 + 60 * i, accepted=300 + 9 * i,
                hwerr=30, rebootcnt=2, good=5000 + 90 * i, mhs=735000.0, temp=68.0, fan=2000)
        if i == 17:
            for k in ("elapsed", "mhs_av", "mhs_20s", "hwerr", "accepted", "clock", "rebootcnt", "nonces_good"):
                r[k] = ""
        rows.append(r)
    # D: fan target 65 -> 70 at 11:41, still 575 MHz, 11:41-12:05, 25 rows, one overheat sample.
    for i in range(25):
        rows.append(row(T0 + timedelta(minutes=101 + i), clock=575.0, elapsed=4300 + 60 * i, accepted=615 + 9 * i,
                        hwerr=30 + i // 4, rebootcnt=2, good=8150 + 90 * i, mhs=735000.0, temp=74.0, fan=1500,
                        target=70, overheat=1 if i == 20 else 0))
    # E: 550 MHz, 12:06-12:15, 10 rows: shorter than 20 min.
    for i in range(10):
        rows.append(row(T0 + timedelta(minutes=126 + i), clock=550.0, elapsed=5800 + 60 * i, accepted=840 + 9 * i,
                        hwerr=36, rebootcnt=2, good=10400 + 80 * i, mhs=715000.0, target=70))
    return rows


def write_csv(path, rows, columns):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(columns) + "\n")
        for r in rows:
            f.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in columns) + "\n")


class SegmentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "log.csv"
        write_csv(cls.path, fixture_rows(), poller.COLUMNS)
        cls.rows = trials.read_rows(cls.path)
        cls.segs = [trials.summarize(s) for s in trials.segments(cls.rows)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rows_with_blank_numbers_are_dropped(self):
        self.assertEqual(len(self.rows), 31 + 25 + 34 + 25 + 10)
        self.assertIsInstance(self.rows[0]["accepted"], int)
        self.assertIsInstance(self.rows[0]["clock"], float)

    def test_boundaries_clock_restart_gap_target(self):
        self.assertEqual([(s["clock"], s["fan_target"], s["samples"]) for s in self.segs],
                         [(600, 65, 31), (600, 65, 25), (575, 65, 34), (575, 70, 25), (550, 70, 10)])
        self.assertEqual(self.segs[0]["start"], "2026-09-08 10:00:00")
        self.assertEqual(self.segs[0]["end"], "2026-09-08 10:30:00")
        self.assertEqual(self.segs[1]["start"], "2026-09-08 10:31:00")
        self.assertEqual(self.segs[2]["start"], "2026-09-08 11:06:00")

    def test_duration_and_short_flag(self):
        self.assertEqual([round(s["minutes"]) for s in self.segs], [30, 24, 34, 24, 9])
        self.assertEqual([s["short"] for s in self.segs], [False, False, False, False, True])
        self.assertTrue(self.segs[-1]["last"])
        self.assertFalse(self.segs[0]["last"])

    def test_worst_chip_bad_share(self):
        a = self.segs[0]
        self.assertEqual(a["worst_chip"], 8)
        self.assertAlmostEqual(a["bad_pct"], 20.0)          # 60 bad / (240 good + 60 bad)
        self.assertAlmostEqual(a["bad_per_hour"], 120.0)    # 60 bad in 30 min
        self.assertFalse(a["bad_partial"])

    def test_chip_that_leaves_the_weak_list_is_partial(self):
        b = self.segs[1]
        self.assertEqual(b["worst_chip"], 8)
        self.assertAlmostEqual(b["bad_pct"], 100.0 * 9 / (72 + 9))   # first 10 rows only
        self.assertTrue(b["bad_partial"])
        self.assertAlmostEqual(b["bad_per_hour"], 9 / (9 / 60))      # 9 bad over the 9 min it was present

    def test_no_weak_chip(self):
        c = self.segs[2]
        self.assertIsNone(c["worst_chip"])
        self.assertIsNone(c["bad_pct"])
        self.assertEqual(c["bad_per_hour"], 0.0)

    def test_board_resets(self):
        self.assertEqual([s["resets"] for s in self.segs], [0, 2, 0, 0, 0])

    def test_hw_error_share_from_nonce_totals(self):
        a = self.segs[0]
        self.assertAlmostEqual(a["hw_pct"], 100.0 * 30 / (3000 + 30))
        self.assertFalse(a["hw_approx"])
        d = self.segs[3]
        self.assertAlmostEqual(d["hw_pct"], 100.0 * 6 / (2160 + 6))

    def test_accepted_per_hour_and_means(self):
        a = self.segs[0]
        self.assertAlmostEqual(a["accepted_per_hour"], 600.0)
        self.assertAlmostEqual(a["mhs"], 750000.0)
        self.assertAlmostEqual(a["chip_temp"], 70.0)
        self.assertAlmostEqual(a["fan_rpm"], 3000.0)
        self.assertEqual(a["overheat"], 0)
        self.assertEqual(self.segs[3]["overheat"], 1)
        self.assertAlmostEqual(self.segs[2]["accepted_per_hour"], 9 * 60.0)

    def test_rollup_pools_segments_by_clock_and_target(self):
        roll = trials.rollup(self.segs)
        self.assertEqual([(r["clock"], r["fan_target"], r["segments"]) for r in roll],
                         [(575, 70, 1), (575, 65, 1), (600, 65, 2)])       # newest first, short E excluded
        r600 = roll[2]
        self.assertAlmostEqual(r600["minutes"], 54.0)
        self.assertEqual(r600["resets"], 2)
        self.assertAlmostEqual(r600["accepted_per_hour"], (300 + 240) / (54 / 60))
        self.assertAlmostEqual(r600["bad_pct_min"], 100.0 * 9 / 81)
        self.assertAlmostEqual(r600["bad_pct_max"], 20.0)
        self.assertEqual(r600["worst_chip"], 8)
        self.assertTrue(r600["bad_partial"])
        self.assertAlmostEqual(r600["hw_pct"], 100.0 * (30 + 24) / (3000 + 2400 + 30 + 24))
        self.assertAlmostEqual(r600["mhs"], (31 * 750000 + 25 * 760000) / 56)
        self.assertEqual(r600["start"], "2026-09-08 10:00:00")
        self.assertEqual(r600["end"], "2026-09-08 10:55:00")

    def test_table_shape(self):
        t = trials.table(self.path)
        self.assertEqual(set(t), {"generated", "rollup", "segments", "min_minutes"})
        self.assertEqual(len(t["rollup"]), 3)
        self.assertEqual([s["clock"] for s in t["segments"]], [550, 575, 575, 600, 600])   # newest first
        self.assertEqual(t["min_minutes"], 20)

    def test_missing_log_gives_empty_table(self):
        t = trials.table(Path(self.tmp.name) / "nope.csv")
        self.assertEqual((t["rollup"], t["segments"]), ([], []))


class OldLogTest(unittest.TestCase):
    """Rows written before the nonce and fan-target columns existed."""

    def test_falls_back_to_firmware_hw_pct_and_unknown_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.csv"
            rows = [row(T0 + timedelta(minutes=i), elapsed=60 * i, accepted=10 * i, hwerr=i, hwerr_pct=0.35 + i / 1000.0,
                        weak="8:%d/%d" % (100 + 8 * i, 2 * i)) for i in range(31)]
            write_csv(path, rows, OLD_COLUMNS)
            segs = [trials.summarize(s) for s in trials.segments(trials.read_rows(path))]
            self.assertEqual(len(segs), 1)
            s = segs[0]
            self.assertIsNone(s["fan_target"])
            self.assertTrue(s["hw_approx"])
            self.assertAlmostEqual(s["hw_pct"], 0.38)          # hwerr_pct of the last row
            self.assertAlmostEqual(s["bad_pct"], 20.0)
            self.assertEqual(s["overheat"], 0)
            roll = trials.rollup(segs)
            self.assertTrue(roll[0]["hw_approx"])
            self.assertIsNone(roll[0]["fan_target"])


class FormatTest(unittest.TestCase):
    def test_text_table_has_one_line_per_rollup_row(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.csv"
            write_csv(path, fixture_rows(), poller.COLUMNS)
            text = trials.format_table(trials.table(path))
            lines = text.splitlines()
            self.assertEqual(len(lines), 1 + 3)                 # header + 3 rollup rows
            self.assertIn("600", lines[3])
            self.assertIn("11.1-20.0%", lines[3].replace(" ", ""))
            self.assertIn("none", lines[2])                     # 575/65 had no weak chip
            text = trials.format_table(trials.table(path), segments=True)
            self.assertEqual(len(text.splitlines()), 1 + 4)     # short E hidden
            text = trials.format_table(trials.table(path, min_minutes=5), segments=True)
            self.assertEqual(len(text.splitlines()), 1 + 5)


class FakeWorld:
    """A fake clock whose `sleep` advances time and appends one log row per simulated minute.

    `script(minute)` returns keyword overrides for `row` at that minute, so a test
    can make the board reset or a chip go bad part-way through a step.
    """

    def __init__(self, path, script=None, clock_mhz=None):
        self.path = path
        self.t = T0
        self.script = script or (lambda m: {})
        self.minute = 0
        self.mhz = clock_mhz            # what the "miner" runs; run_trial changes it through set_plan
        self.interrupt_at = None
        self.stale_after = None
        write_csv(path, [], poller.COLUMNS)

    def now(self):
        return self.t.timestamp()

    def sleep(self, seconds):
        steps = max(1, int(round(seconds / 60.0)))
        for _ in range(steps):
            self.t += timedelta(minutes=1)
            self.minute += 1
            if self.interrupt_at is not None and self.minute >= self.interrupt_at:
                raise KeyboardInterrupt()
            if self.stale_after is not None and self.minute > self.stale_after:
                continue
            kw = dict(clock=float(self.mhz), elapsed=60 * self.minute, accepted=10 * self.minute, hwerr=self.minute,
                      good=1000 * self.minute, weak="8:%d/%d" % (100 * self.minute, self.minute))
            kw.update(self.script(self.minute))
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                r = row(self.t, **kw)
                f.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in poller.COLUMNS) + "\n")


class RunTrialTest(unittest.TestCase):
    def setUp(self):
        from tests.fake_miner import FakeMiner
        from gbox import api
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.miner = api.Miner(self.fm.address, password="password")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "log.csv"
        self.status = Path(self.tmp.name) / "trial.json"
        self.reports = []
        self.plans = []
        real = self.miner.set_plan

        def set_plan(mhz, volts=None):
            self.plans.append(mhz)
            self.world.mhz = mhz
            return real(mhz, volts)
        self.miner.set_plan = set_plan

    def run_it(self, clocks, world, **kw):
        self.world = world
        world.mhz = world.mhz or 600
        args = dict(hours=1.0, settle_min=5, check_min=5, min_judge_min=10, log_path=self.log, status_path=self.status,
                    report=self.reports.append, now=world.now, sleep=world.sleep, out=lambda *a: None)
        args.update(kw)
        return trials.run_trial(self.miner, clocks, **args)

    def test_steps_through_the_list_and_ends_on_the_end_clock(self):
        world = FakeWorld(self.log)
        result = self.run_it([550, 575], world, end=525)
        self.assertEqual(self.plans, [550, 575, 525])
        self.assertTrue(result["completed"])
        self.assertIsNone(result["reason"])
        self.assertIn("525 MHz", self.miner.setting()["manualPowerplan"])
        self.assertFalse(self.status.exists())
        self.assertTrue(any("step 1/2" in r and "550" in r for r in self.reports))
        self.assertTrue(any("finished" in r and "525" in r for r in self.reports))
        # each step ran its settle plus its hold: 2 x (5 + 60) minutes, give or take a check
        self.assertGreaterEqual(world.minute, 130)
        self.assertLess(world.minute, 145)

    def test_end_defaults_to_the_lowest_clock(self):
        world = FakeWorld(self.log)
        self.run_it([600, 550, 575], world, hours=0.1)
        self.assertEqual(self.plans[-1], 550)

    def test_board_reset_aborts_the_run(self):
        world = FakeWorld(self.log, script=lambda m: {"rebootcnt": 1 if m >= 20 else 0})
        result = self.run_it([550, 575, 600], world, end=500)
        self.assertEqual(self.plans, [550, 500])                # never reached 575
        self.assertFalse(result["completed"])
        self.assertEqual(result["step"], 1)
        self.assertIn("reset", result["reason"])
        self.assertTrue(any("aborted" in r for r in self.reports))
        self.assertFalse(self.status.exists())

    def test_bad_share_aborts_only_after_the_judging_delay(self):
        # chip 8 at 10 percent bad from the start of the step; the guard waits min_judge_min before it counts
        world = FakeWorld(self.log, script=lambda m: {"weak": "8:%d/%d" % (900 * m, 100 * m)})
        result = self.run_it([550, 575], world, end=500, max_bad=5.0)
        self.assertEqual(self.plans, [550, 500])
        self.assertIn("bad share", result["reason"])
        self.assertGreaterEqual(world.minute, 5 + 10)           # settle + judging delay before the abort
        self.assertLess(world.minute, 5 + 10 + 2 * 5 + 1)

    def test_clean_chip_below_threshold_does_not_abort(self):
        world = FakeWorld(self.log, script=lambda m: {"weak": "8:%d/%d" % (1000 * m, 2 * m)})   # 0.2 percent
        result = self.run_it([550], world, end=550, max_bad=1.0, hours=0.5)
        self.assertTrue(result["completed"])

    def test_ctrl_c_sets_the_end_clock(self):
        world = FakeWorld(self.log)
        world.interrupt_at = 30
        result = self.run_it([550, 575], world, end=500)
        self.assertEqual(self.plans, [550, 500])
        self.assertFalse(result["completed"])
        self.assertIn("interrupted", result["reason"])
        self.assertFalse(self.status.exists())

    def test_stale_log_aborts(self):
        world = FakeWorld(self.log)
        world.stale_after = 20                                   # the service stops writing after minute 20
        result = self.run_it([550, 575], world, end=500, stale_min=15)
        self.assertEqual(self.plans, [550, 500])
        self.assertIn("no fresh samples", result["reason"])

    def test_bad_clock_list_is_refused_before_anything_is_sent(self):
        world = FakeWorld(self.log)
        with self.assertRaises(ValueError):
            self.run_it([550, 730], world)
        with self.assertRaises(ValueError):
            self.run_it([560], world)
        with self.assertRaises(ValueError):
            self.run_it([550], world, end=740)
        with self.assertRaises(ValueError):
            self.run_it([], world)
        self.assertEqual(self.plans, [])

    def test_fan_target_is_set_once_at_the_start(self):
        world = FakeWorld(self.log)
        self.run_it([550], world, hours=0.1, fan=70)
        self.assertEqual(self.miner.setting()["temp_target"], 70)
        self.assertTrue(any("fan target" in r and "70" in r for r in self.reports))

    def test_status_file_tracks_the_step(self):
        import json
        seen = []
        world = FakeWorld(self.log)
        real_sleep = world.sleep

        def sleep(s):
            if self.status.exists():
                seen.append(json.loads(self.status.read_text(encoding="utf-8")))
            real_sleep(s)
        self.run_it([550, 575], world, end=500, hours=0.25, sleep=sleep)
        self.assertTrue(seen)
        self.assertEqual(seen[0]["step"], 1)
        self.assertEqual(seen[0]["clocks"], [550, 575])
        self.assertEqual(seen[0]["clock"], 550)
        self.assertEqual(seen[0]["end"], 500)
        self.assertEqual(seen[0]["hours"], 0.25)
        self.assertIn(seen[0]["status"], ("settling", "holding"))
        self.assertEqual(seen[-1]["step"], 2)
        self.assertEqual(seen[-1]["status"], "holding")
        self.assertEqual(set(seen[-1]) >= {"started", "step_started", "step_ends", "checks"}, True)


if __name__ == "__main__":
    unittest.main()
