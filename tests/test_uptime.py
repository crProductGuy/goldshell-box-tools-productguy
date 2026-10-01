"""gbox.uptime: restarts found from the uptime counter, one cause each, runs, counts and time hashing.

Every cause case is shaped on a real restart of the SC-BOX between 2026-09-23 and 2026-09-27 (the
uptime-strip mockup's table); the replay of the real log is in the session's evidence, not here.
"""
import datetime
import tempfile
import unittest
from pathlib import Path

from gbox import markers, uptime

NOW = datetime.datetime(2026, 10, 1, 0, 0, 0)
STEP = 30


class Log:
    """A synthetic log: good samples every 30 s with a rising uptime, and the outages a test adds."""

    def __init__(self, start, elapsed=3600, watts=158.0, clock=525.0):
        self.rows, self.t, self.elapsed, self.watts, self.clock, self.lines = [], start, elapsed, watts, clock, []

    def good(self, n=1):
        for _ in range(n):
            self.rows.append({"t": self.t, "ok": True, "elapsed": self.elapsed, "mhs_20s": 670000.0,
                              "clock": self.clock, "watts": self.watts})
            self.t += datetime.timedelta(seconds=STEP)
            self.elapsed += STEP
        return self

    def failed(self, watts=None, n=1):
        for _ in range(n):
            self.rows.append({"t": self.t, "ok": False, "elapsed": None, "mhs_20s": None, "clock": None, "watts": watts})
            self.t += datetime.timedelta(seconds=STEP)
        return self

    def boot(self, up=15):
        """The miner is back: its uptime starts over."""
        self.elapsed = up
        return self

    def event(self, text, offset=0):
        self.lines.append((self.t + datetime.timedelta(seconds=offset)).strftime(uptime.STAMP) + " " + text)
        return self

    def skip(self, seconds):
        """gbox not sampling: the clock moves on, the miner's uptime too."""
        self.t += datetime.timedelta(seconds=seconds)
        self.elapsed += seconds
        return self

    def report(self, now=None):
        return uptime.report(self.rows, self.lines, now=now or self.t)


def recent():
    return Log(NOW - datetime.timedelta(hours=10))


class CauseTest(unittest.TestCase):
    def one(self, log):
        r = log.report()
        self.assertEqual(len(r["restarts"]), 1, r["restarts"])
        return r["restarts"][0]

    def test_restarted_by_itself_while_powered(self):
        """09-27 09:10: one failed poll at 27 W, no gbox line: the miner's own."""
        log = recent().good(20).failed(watts=26.7).boot().good(5)
        x = self.one(log)
        self.assertEqual(x["cause"], "own")
        self.assertEqual(x["watts_min"], 26.7)
        self.assertEqual(x["clock"], 525.0)
        self.assertAlmostEqual(x["up_before_h"], (3600 + 19 * STEP) / 3600.0, places=2)
        self.assertEqual(x["events"], [])

    def test_lost_power_reads_under_8_watts(self):
        """09-25 17:46, the windstorm: 2.6 to 2.8 W while down."""
        x = self.one(recent().good(20).failed(watts=2.6).failed(watts=2.8).boot().good(5))
        self.assertEqual((x["cause"], x["watts_min"], x["watts_max"]), ("power", 2.6, 2.8))

    def test_exactly_8_watts_is_powered(self):
        x = self.one(recent().good(20).failed(watts=uptime.POWER_LOST_WATTS).boot().good(5))
        self.assertEqual(x["cause"], "own")

    def test_hung_while_powered_and_the_watchdog_cycled_it(self):
        """09-26 20:05: 25 W while hung, two failed soft restarts, then a cycle whose off period reads 0 W.

        The 0 W of gbox's own power cut must not make the hang look like a power loss.
        """
        log = recent().good(20).failed(watts=25.4, n=4)
        log.event("watchdog: restart attempt failed: PUT mcb/restart: timed out").failed(watts=24.5, n=4)
        log.event("power: cycled #3 in 24 h: off 120 s, on (miner unreachable for 2 min; 31 W before)")
        log.failed(watts=0.0, n=4).boot().good(5)
        x = self.one(log)
        self.assertEqual(x["cause"], "hung")
        self.assertEqual((x["watts_min"], x["watts_max"]), (24.5, 25.4))
        self.assertEqual(len(x["events"]), 2)

    def test_a_cycle_after_a_power_loss_is_power_not_hung(self):
        """The plug read 3 W before gbox acted: the miner had no power, and the cycle only brought it back."""
        log = recent().good(20).failed(watts=3.0, n=4)
        log.event("power: cycled #1 in 24 h: off 120 s, on (miner unreachable for 2 min; 3 W before)")
        log.failed(watts=0.0, n=4).boot().good(5)
        self.assertEqual(self.one(log)["cause"], "power")

    def test_power_lost_ahead_of_the_plug(self):
        """09-25 22:57: the plug stopped answering too; it came back hung at 25 W and the watchdog cycled it."""
        log = recent().good(20).failed(watts=None).event("power: plug unreachable (did not answer: TimeoutError)")
        log.failed(watts=25.0, n=6).event("power: cycled #1 in 24 h: off 120 s, on").failed(watts=0.0, n=4).boot().good(5)
        self.assertEqual(self.one(log)["cause"], "power")

    def test_you_switched_it_from_the_page(self):
        """09-25 23:07: you switched the plug off and on; that wins over the plug's absence and any watts."""
        log = recent().good(20).event("power: switched off by you (page; 159 W before)").failed(watts=0.0, n=6)
        log.event("power: plug unreachable (did not answer)").event("power: switched on by you (page)").boot().good(5)
        self.assertEqual(self.one(log)["cause"], "you")

    def test_you_by_hand_and_from_the_dashboard(self):
        for line in ("power: switched off by hand (cli)", "dashboard: soft restart sent", "dashboard: clock set to 525 MHz"):
            with self.subTest(line=line):
                log = recent().good(20).event(line).failed(watts=20.0).boot().good(5)
                self.assertEqual(self.one(log)["cause"], "you")

    def test_a_note_from_the_dashboard_is_not_a_cause(self):
        log = recent().good(20).event("dashboard: observation, the room is warm").failed(watts=20.0).boot().good(5)
        x = self.one(log)
        self.assertEqual(x["cause"], "own")
        self.assertEqual(x["events"], [])

    def test_not_seen_when_gbox_itself_started(self):
        """09-24 16:13: gbox was off from 16:11 to 16:47; the moment comes from the uptime counter alone."""
        log = recent().good(20).skip(36 * 60)
        restart_at = log.t - datetime.timedelta(seconds=2061)
        log.boot(up=2061).event("service: started v0.10.1, poll 30s").good(5)
        x = self.one(log)
        self.assertEqual(x["cause"], "unseen")
        self.assertEqual(x["t"], restart_at.strftime(uptime.STAMP))

    def test_not_seen_across_a_sampling_gap_without_a_service_line(self):
        """The PC slept: no failed poll, no service start, a gap longer than a sampling gap, the uptime fell."""
        log = recent().good(20).skip(markers.UNSAMPLED_SECONDS + 60).boot(up=100).good(5)
        self.assertEqual(self.one(log)["cause"], "unseen")

    def test_no_wall_reading_while_down_is_unmeasured(self):
        x = self.one(recent().good(20).failed(watts=None, n=2).boot().good(5))
        self.assertEqual((x["cause"], x["watts_min"]), ("unmeasured", None))

    def test_back_within_one_poll_has_no_reading_either(self):
        x = self.one(recent().good(20).boot().good(5))
        self.assertEqual(x["cause"], "unmeasured")


class DetectionTest(unittest.TestCase):
    def test_a_sampling_gap_with_the_miner_still_up_is_not_a_restart(self):
        r = recent().good(20).skip(3600).good(5).report()
        self.assertEqual(r["restarts"], [])
        self.assertEqual(len(r["runs"]), 1)

    def test_the_pc_clock_stepping_forward_is_not_a_restart(self):
        """Between two back-to-back polls. A step longer than a sampling gap reads as gbox missing that
        time, as it does in gbox.markers: without a monotonic clock in the log the two cannot be told apart."""
        log = recent().good(20)
        log.t += datetime.timedelta(seconds=120)    # the PC's clock jumps; the miner's uptime does not
        self.assertEqual(log.good(5).report()["restarts"], [])

    def test_uptime_jitter_inside_the_slack_is_not_a_restart(self):
        log = recent().good(20)
        log.elapsed -= markers.RESTART_SLACK_SECONDS - 5
        self.assertEqual(log.good(5).report()["restarts"], [])

    def test_a_restart_behind_a_long_outage_is_caught_by_the_start_moving(self):
        """Down for two hours while gbox polled; back for longer than it had been up, so the uptime did not fall."""
        log = Log(NOW - datetime.timedelta(hours=10), elapsed=600).good(4).failed(watts=20.0, n=240)
        log.boot(up=3000).good(5)
        self.assertEqual(len(log.report()["restarts"]), 1)


    def test_a_restart_while_gbox_was_off_is_caught_even_when_the_uptime_did_not_fall(self):
        """Up 10 min, gbox off for 2 h, the miner restarted an hour in: it reads 3,600 s, more than before."""
        log = Log(NOW - datetime.timedelta(hours=10), elapsed=600).good(1).skip(7200).boot(up=3600).good(3)
        r = log.report()
        self.assertEqual(len(r["restarts"]), 1)
        self.assertEqual(r["restarts"][0]["cause"], "unseen")


class WindowTest(unittest.TestCase):
    def build(self):
        """Restarts 9, 7.5 and 1 days ago; the record is read at the end of the log."""
        log = Log(NOW - datetime.timedelta(days=10), elapsed=3600)
        log.good(2)
        for days_ago in (9, 7.5, 1):
            log.skip(int((NOW - datetime.timedelta(days=days_ago) - log.t).total_seconds()))
            log.good(1).failed(watts=20.0).boot().good(2)
        log.skip(int((NOW - log.t).total_seconds())).good(1)
        return log

    def test_eight_days_listed_seven_counted(self):
        r = self.build().report()
        self.assertEqual(len(r["restarts"]), 2)                         # 9 days ago is outside the window
        self.assertEqual([x["counted"] for x in r["restarts"]], [True, False])
        self.assertEqual(r["counts"]["own"], 1)
        self.assertEqual(r["counts"]["total"], 1)
        self.assertEqual(r["counts"]["miner"], 1)
        self.assertEqual((r["days"], r["count_days"]), (8, 7))

    def test_runs_that_reach_into_the_window_are_kept_whole(self):
        r = self.build().report()
        first = r["runs"][0]
        self.assertLess(first["start"], r["from"])                      # began 9 days ago: shown, not clipped here
        self.assertTrue(r["runs"][-1]["ongoing"])

    def test_counts_hold_every_cause_and_the_two_sums(self):
        counts = self.build().report()["counts"]
        self.assertEqual(set(counts), set(uptime.CAUSES) | {"total", "miner"})


class RunTest(unittest.TestCase):
    def test_current_run_and_longest(self):
        log = recent().good(20).failed(watts=20.0).boot(up=15).good(400)
        r = log.report()
        self.assertTrue(r["current"])
        self.assertEqual(r["current"]["clock"], 525.0)
        self.assertAlmostEqual(r["current"]["hours"], (15 + 400 * STEP - STEP) / 3600.0 + STEP / 3600.0, places=1)
        self.assertEqual(r["longest"]["start"], r["current"]["start"])
        self.assertTrue(r["longest"]["ongoing"])

    def test_no_current_run_while_the_miner_is_down(self):
        r = recent().good(20).failed(watts=20.0, n=3).report()
        self.assertIsNone(r["current"])
        self.assertFalse(r["runs"][-1]["ongoing"])

    def test_no_current_run_when_gbox_stopped_sampling(self):
        log = recent().good(20)
        r = log.report(now=log.t + datetime.timedelta(hours=1))
        self.assertIsNone(r["current"])

    def test_an_empty_log(self):
        r = uptime.report([], [], now=NOW)
        self.assertEqual((r["runs"], r["restarts"], r["current"], r["longest"]), ([], [], None, None))
        self.assertIsNone(r["hashing"]["pct"])


class HashingTimeTest(unittest.TestCase):
    def test_up_down_and_not_sampled(self):
        log = recent().good(11)                         # 10 intervals hashing
        log.failed(watts=20.0, n=4)                     # the last good one's interval plus 3 failed: 4 down ...
        log.boot().good(1).skip(3600).good(1)           # ... and an hour of gbox off between two good samples
        h = uptime.hashing_time(log.rows, log.rows[0]["t"], log.rows[-1]["t"])
        self.assertEqual(h["up_s"], 10 * STEP + STEP)   # 10 before, 1 from the first good after the boot ...
        self.assertEqual(h["down_s"], 4 * STEP)
        self.assertEqual(h["unsampled_s"], 3600 + STEP)
        self.assertAlmostEqual(h["pct"], 100.0 * 11 / 15)

    def test_a_long_gap_beside_a_failed_poll_is_down(self):
        """The watchdog's ladder slows the polls to minutes apart while the miner is out: still down."""
        log = recent().good(2).failed(watts=20.0)
        log.t += datetime.timedelta(seconds=markers.UNSAMPLED_SECONDS + 30)
        log.failed(watts=20.0).boot().good(2)
        h = uptime.hashing_time(log.rows, log.rows[0]["t"], log.rows[-1]["t"])
        self.assertGreater(h["down_s"], markers.UNSAMPLED_SECONDS)
        self.assertEqual(h["unsampled_s"], 0)

    def test_a_good_sample_reading_zero_hashrate_is_down(self):
        log = recent().good(3)
        log.rows[1]["mhs_20s"] = 0.0
        h = uptime.hashing_time(log.rows, log.rows[0]["t"], log.rows[-1]["t"])
        self.assertEqual((h["up_s"], h["down_s"]), (STEP, STEP))


class ReadRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "log.csv"

    def test_reads_six_columns_from_a_day_before_the_window(self):
        old = (NOW - datetime.timedelta(days=uptime.WINDOW_DAYS + 2)).strftime(uptime.STAMP)
        edge = (NOW - datetime.timedelta(days=uptime.WINDOW_DAYS, hours=12)).strftime(uptime.STAMP)
        new = (NOW - datetime.timedelta(hours=1)).strftime(uptime.STAMP)
        self.path.write_text("time,http,elapsed,mhs_av,mhs_20s,clock,watts,chips\n"
                             + old + ",ok,10,1,2,525,150,x\n"
                             + edge + ",ok,20,1,2,525,151,x\n"
                             + "not a time,ok,30,1,2,525,152,x\n"
                             + new + ",ERR:timeout,,,,,3.5,\n", encoding="utf-8")
        rows = uptime.read_rows(self.path, now=NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"t": datetime.datetime.strptime(edge, uptime.STAMP), "ok": True, "elapsed": 20,
                                   "mhs_20s": 2.0, "clock": 525.0, "watts": 151.0})
        self.assertEqual((rows[1]["ok"], rows[1]["elapsed"], rows[1]["watts"]), (False, None, 3.5))

    def test_a_missing_log_is_no_rows(self):
        self.assertEqual(uptime.read_rows(self.path, now=NOW), [])


if __name__ == "__main__":
    unittest.main()
