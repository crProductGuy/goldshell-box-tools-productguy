"""gbox.markers: the miner-restarted and fans-high event lines (0.10.2)."""
import datetime
import tempfile
import unittest
from pathlib import Path

from gbox import api, markers, poller
from gbox.events import EventLog
from tests.fake_miner import FakeMiner

T0 = datetime.datetime(2026, 9, 27, 9, 0, 0).timestamp()
SETTLED = markers.SETTLE_MINUTES * 60


def ok(elapsed, fan0=1260, fan1=1200, temp=63.0):
    return {"http": "ok", "elapsed": elapsed, "fan0": fan0, "fan1": fan1, "tstemp0": temp}


ERR = {"http": "ERR:MinerError:devs 4028: TimeoutError: timed out"}


class Stamped(EventLog):
    """An EventLog whose lines carry the test's clock, not the wall clock, so causes can be placed in time."""
    def __init__(self, path=None):
        super().__init__(path)
        self.now = T0

    def write(self, message):
        line = "%s %s" % (datetime.datetime.fromtimestamp(self.now).strftime("%Y-%m-%d %H:%M:%S"), message)
        with self._lock:
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            self.recent.append(line)
            self.written += 1
        return line


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ev = Stamped(Path(self.tmp.name) / "events.log")
        self.m = markers.Markers(self.ev)

    def feed(self, t, row, fan_max=None):
        self.ev.now = t
        self.m.observe(row, t, fan_max)

    def lines(self, prefix):
        return [l for l in self.ev.recent if l[20:].startswith(prefix)]

    def settle(self, t=T0, elapsed=SETTLED, minutes=40, fan0=1260, fan1=1200):
        """A settled run: `minutes` of good samples, 30 s apart, from uptime `elapsed`. Returns (t, elapsed) next."""
        for i in range(minutes * 2):
            self.feed(t + i * 30, ok(elapsed + i * 30, fan0, fan1))
        return t + minutes * 60, elapsed + minutes * 60


class RestartTest(Base):
    def test_uptime_advancing_with_jitter_is_not_a_restart(self):
        for i, jitter in enumerate([0, 7, -12, 25, -30, 3]):
            self.feed(T0 + i * 30, ok(5000 + i * 30 + jitter))
        self.assertEqual(self.lines("miner:"), [])

    def test_quick_reboot_on_its_own(self):
        # 2026-09-27 09:10 on the SC-BOX: one refused poll, back 30 s later with 11 s of uptime
        self.feed(T0, ok(47071))
        self.feed(T0 + 30, ERR)
        self.feed(T0 + 60, ok(11))
        [line] = self.lines("miner:")
        self.assertIn("miner: restarted on its own; mining uptime had been 13 h 4 min, now 11 s; unreachable for 30 s", line)

    def test_restart_between_two_polls_says_so(self):
        self.feed(T0, ok(4000))
        self.feed(T0 + 30, ok(12))
        [line] = self.lines("miner:")
        self.assertIn("never seen unreachable", line)

    def test_restart_hidden_behind_a_long_outage(self):
        # the new uptime (10 min) is larger than the old one (1 min): comparing uptimes would miss it
        self.feed(T0, ok(60))
        self.feed(T0 + 30, ERR)
        self.feed(T0 + 25 * 60, ok(600))
        [line] = self.lines("miner:")
        self.assertIn("mining uptime had been 1 min, now 10 min; unreachable for 24 min", line)

    def test_gbox_not_sampling_is_named(self):
        self.feed(T0, ok(20000))
        self.feed(T0 + 37 * 60, ok(600))
        [line] = self.lines("miner:")
        self.assertIn("gbox was not sampling for 37 min", line)

    def test_cause_quoted_from_the_event_log(self):
        self.feed(T0, ok(30662))
        self.feed(T0 + 30, ERR)
        self.ev.now = T0 + 600
        self.ev.write("watchdog: restart attempt failed: PUT mcb/restart: timed out (miner unreachable for 2 min)")
        self.ev.write("power: cycled #3 in 24 h: off 120 s, on (miner unreachable for 2 min; 31 W before)")
        self.feed(T0 + 700, ok(18))
        [line] = self.lines("miner:")
        # the power cycle outranks the failed soft restart written before it
        self.assertIn("miner: restarted after [power: cycled #3 in 24 h", line)

    def test_cause_outside_the_window_is_ignored(self):
        self.ev.now = T0 - 3600
        self.ev.write("power: cycled #1 in 24 h: off 120 s, on")
        self.feed(T0, ok(4000))
        self.feed(T0 + 30, ERR)
        self.feed(T0 + 60, ok(12))
        [line] = self.lines("miner:")
        self.assertIn("on its own", line)

    def test_a_forward_clock_step_is_not_a_restart(self):
        # 0.10.2 review: this PC's clock jumping 2 min between two polls moved the implied start by 2 min
        self.feed(T0, ok(50000))
        self.feed(T0 + 150, ok(50030))
        self.assertEqual(self.lines("miner:"), [])

    def test_a_boot_loop_counts_every_restart(self):
        # 0.10.2 review: runs of about a minute, each followed by 30 s unreachable
        t = T0
        self.feed(t, ok(5000))
        for loop in range(5):
            self.feed(t + 30, ERR)
            self.feed(t + 60, ok(5))
            self.feed(t + 90, ok(35))
            self.feed(t + 120, ok(65))
            t += 120
        self.assertEqual(len(self.lines("miner:")), 5)

    def test_waiting_for_a_token_is_not_unreachable(self):
        self.feed(T0, ok(4000))
        self.feed(T0 + 30, {"http": "ERR:NoCredentials:waiting for a token from the dashboard"})
        self.feed(T0 + 60, ok(12))
        [line] = self.lines("miner:")
        self.assertIn("never seen unreachable", line)

    def test_a_failed_soft_restart_is_quoted_last(self):
        # a timed-out request may still have reached the miner: quoted, never "on its own", and outranked
        self.feed(T0, ok(4000))
        self.feed(T0 + 30, ERR)
        self.ev.now = T0 + 60
        self.ev.write("watchdog: restart attempt failed: PUT mcb/restart: timed out (miner unreachable for 2 min)")
        self.feed(T0 + 90, ok(12))
        self.assertIn("after [watchdog: restart attempt failed", self.lines("miner:")[0])
        self.ev.now = T0 + 120
        self.ev.write("watchdog: restart attempt failed: PUT mcb/restart: timed out")
        self.ev.write("watchdog: restart #2 sent (miner unreachable for 2 min)")
        self.feed(T0 + 150, ERR)
        self.feed(T0 + 180, ok(8))
        self.assertIn("after [watchdog: restart #2 sent", self.lines("miner:")[1])

    def test_first_sample_after_start_is_not_a_restart(self):
        self.feed(T0, ok(11))
        self.assertEqual(self.lines("miner:"), [])

    def test_malformed_rows_do_not_raise(self):
        for row in ({}, {"http": "ok"}, {"http": "ok", "elapsed": None}, {"http": "ok", "elapsed": "x"},
                    {"http": "ok", "elapsed": 99999, "fan0": "fast", "fan1": None}):
            self.feed(T0, row)
        self.assertEqual(self.lines("miner:") + self.lines("fans:"), [])


class FansTest(Base):
    def test_boot_ramp_is_not_high(self):
        # 2026-09-27 09:10: 4,380 RPM at 11 s of uptime, down to 1,260 by 15 min; a fresh run
        self.feed(T0, ok(47071))
        t = T0 + 30
        for i in range(60):
            self.feed(t + i * 30, ok(11 + i * 30, fan0=4380 - i * 52, fan1=4380 - i * 53))
        self.assertEqual(self.lines("fans:"), [])

    def test_no_verdict_before_a_baseline(self):
        self.feed(T0, ok(SETTLED, fan0=1260))
        self.feed(T0 + 30, ok(SETTLED + 30, fan0=4300))
        self.assertEqual(self.lines("fans:"), [])

    def test_high_then_back_to_normal(self):
        t, el = self.settle()
        self.feed(t, ok(el, fan0=3840, fan1=3900))
        self.assertEqual(self.lines("fans:"), [])                  # one reading over the line is not an episode
        self.feed(t + 30, ok(el + 30, fan0=4000, fan1=3950))
        [high] = self.lines("fans: high")
        self.assertIn("fans: high, 4000 / 3950 RPM against 1260 / 1200 RPM settled in this run; board 63 C", high)
        for i in range(2, 2 + markers.NORMAL_SAMPLES):
            self.feed(t + i * 30, ok(el + i * 30))
        [back] = self.lines("fans: back")
        self.assertIn("fans: back to normal after 1 min (peak 4000 RPM)", back)

    def test_one_calm_sample_does_not_end_it(self):
        t, el = self.settle()
        self.feed(t, ok(el, fan0=3800))
        self.feed(t + 30, ok(el + 30, fan0=3800))
        self.feed(t + 60, ok(el + 60))
        self.feed(t + 90, ok(el + 90, fan0=3800))
        self.assertEqual(len(self.lines("fans: high")), 1)
        self.assertEqual(self.lines("fans: back"), [])

    def test_small_rise_is_not_high(self):
        # a slow-fan model on a warmer afternoon: 1.56x, but only +450 RPM, under HIGH_DELTA_RPM
        t, el = self.settle(fan0=800, fan1=800)
        self.feed(t, ok(el, fan0=1250, fan1=1250))
        self.assertEqual(self.lines("fans:"), [])

    def test_boot_samples_stay_out_of_the_baseline(self):
        # after a restart: 45 min near full speed, then 31 min settled. Counting every sample would put the
        # median at full speed; leaving out the first SETTLE_MINUTES leaves the settled speed in the majority.
        self.feed(T0, ok(47071))
        t = T0 + 30
        for i in range(90):
            self.feed(t + i * 30, ok(11 + i * 30, fan0=4380, fan1=4380))
        t, el = self.settle(t + 2700, 11 + 2700, minutes=31)
        self.feed(t, ok(el, fan0=2000, fan1=2000))
        self.feed(t + 30, ok(el + 30, fan0=2000, fan1=2000))
        self.assertIn("against 1260 / 1200 RPM", self.lines("fans: high")[0])

    def test_near_rated_maximum_counts_where_the_model_has_one(self):
        t, el = self.settle(fan0=3500, fan1=3500)
        for i in range(2):
            self.feed(t + i * 30, ok(el + i * 30, fan0=4450, fan1=4450))   # 1.27x: not high by the baseline
        self.assertEqual(self.lines("fans:"), [])
        for i in range(2, 4):
            self.feed(t + i * 30, ok(el + i * 30, fan0=4450, fan1=4450), fan_max=4900)
        self.assertEqual(len(self.lines("fans: high")), 1)

    def test_a_model_that_settles_near_its_maximum_is_not_high(self):
        # 0.10.2 review: an SC Lite-like unit rated 2,200 RPM that settles at 2,050
        t, el = self.settle(fan0=2050, fan1=2050)
        for i in range(10):
            self.feed(t + i * 30, ok(el + i * 30, fan0=2050, fan1=2050), fan_max=2200)
        self.assertEqual(self.lines("fans:"), [])

    def test_settings_change_starts_a_new_run(self):
        # 2026-09-23 18:15:58: clock set from the page, fans at 4,320 RPM with the uptime NOT reset
        t, el = self.settle()
        self.ev.now = t
        self.ev.write('dashboard: clock set to 525 MHz (plan "525 MHz 0.41 V 90 RPM 90 RPM", was "550 MHz ...")')
        for i in range(1, 25):
            self.feed(t + i * 30, ok(el + i * 30, fan0=4320 - i * 100, fan1=4320 - i * 100))
        self.assertEqual(self.lines("fans:"), [])

    def test_restart_ends_an_episode_and_resets_the_baseline(self):
        t, el = self.settle()
        self.feed(t, ok(el, fan0=4000))
        self.feed(t + 15, ok(el + 15, fan0=4000))
        self.feed(t + 30, ok(12, fan0=4380))
        self.assertIn("ended by the restart", self.lines("fans: back")[0])
        # the old baseline is gone: a settled new run at a higher speed is not "high"
        t2, el2 = self.settle(t + 60, SETTLED + 40, minutes=40, fan0=2400, fan1=2400)
        self.feed(t2, ok(el2, fan0=2400, fan1=2400))
        self.assertEqual(len(self.lines("fans: high")), 1)
        self.assertEqual(len(self.lines("miner:")), 1)

    def test_single_fan_model(self):
        t, el = self.settle(fan1=None)
        self.feed(t, ok(el, fan0=3900, fan1=None))
        self.feed(t + 30, ok(el + 30, fan0=3900, fan1=None))
        self.assertIn("3900 RPM against 1260 RPM", self.lines("fans: high")[0])


    def test_a_hunting_fan_writes_one_pair_not_one_every_few_minutes(self):
        # 0.10.2 review: 2,000 RPM on every 4th sample for an hour gave 60 lines
        t, el = self.settle()
        for i in range(120):
            f = 2000 if i % 4 in (0, 1) else 1260
            self.feed(t + i * 30, ok(el + i * 30, fan0=f, fan1=f))
        self.assertLessEqual(len(self.lines("fans: high")), 2)     # at most one per REARM_MINUTES

    def test_one_odd_reading_writes_nothing(self):
        t, el = self.settle()
        self.feed(t, ok(el, fan0=4900))
        for i in range(1, 6):
            self.feed(t + i * 30, ok(el + i * 30))
        self.assertEqual(self.lines("fans:"), [])

    def test_a_long_episode_becomes_the_new_level(self):
        # 0.10.2 review: flagged at 2,000, then 12 h at 1.35x never ended and froze the baseline
        t, el = self.settle()
        for i in range(2):
            self.feed(t + i * 30, ok(el + i * 30, fan0=2000, fan1=2000))
        for i in range(2, 2 + 12 * 120):
            self.feed(t + i * 30, ok(el + i * 30, fan0=1700, fan1=1620))
        [back] = self.lines("fans: back")
        self.assertIn("new settled level", back)
        self.assertEqual(len(self.lines("fans: high")), 1)         # and the new level is not flagged again


def hashing(elapsed, fan0=1260, fan1=1200, mhs=680000.0, **kw):
    row = ok(elapsed, fan0, fan1, **kw)
    row["mhs_20s"] = mhs
    return row


class FanStoppedTest(Base):
    """0.10.4: a fan under STOPPED_RPM while hashing."""

    def run_for(self, t, el, n, **kw):
        for i in range(n):
            self.feed(t + i * 30, hashing(el + i * 30, **kw))
        return t + n * 30, el + n * 30

    def test_a_stopped_fan_writes_one_line_after_two_polls(self):
        t, el = self.run_for(T0, 600, 4)
        self.feed(t, hashing(el, fan1=0))
        self.assertEqual(self.lines("fans:"), [])
        self.run_for(t + 30, el + 30, 20, fan1=0)
        [line] = self.lines("fans:")
        self.assertEqual(line[20:], "fans: fan1 stopped while hashing, 0 RPM; fan0 at 1260 RPM; board 63 C")

    def test_turning_again_says_how_long(self):
        t, el = self.run_for(T0, 600, 4)
        t, el = self.run_for(t, el, 20, fan0=0)            # stopped line at the second of these
        self.feed(t, hashing(el, fan0=1300))
        self.assertEqual(self.lines("fans: fan0 turning again")[0][20:], "fans: fan0 turning again after 9 min, 1300 RPM")

    def test_one_zero_reading_writes_nothing(self):
        t, el = self.run_for(T0, 600, 4)
        self.feed(t, hashing(el, fan0=0))
        self.run_for(t + 30, el + 30, 10)
        self.assertEqual(self.lines("fans:"), [])

    def test_a_fan_never_seen_turning_is_not_judged(self):
        # a one-fan model: the second column reads 0 for ever
        self.run_for(T0, 600, 40, fan1=0)
        self.assertEqual(self.lines("fans:"), [])

    def test_not_hashing_is_not_this_rules_business(self):
        # the known cold-start failure: controller up, hashboard dead, fans spinning down
        t, el = self.run_for(T0, 600, 4)
        self.run_for(t, el, 20, fan0=0, fan1=0, mhs=0.0)
        self.assertEqual(self.lines("fans:"), [])

    def test_a_non_hashing_poll_breaks_the_run(self):
        t, el = self.run_for(T0, 600, 4)
        for i in range(10):
            self.feed(t + i * 30, hashing(el + i * 30, fan1=0, mhs=680000.0 if i % 2 else 0.0))
        self.assertEqual(self.lines("fans:"), [])

    def test_a_fan_that_stays_dead_across_a_restart_writes_one_line(self):
        t, el = self.run_for(T0, 3600, 4)
        t, el = self.run_for(t, el, 4, fan1=0)
        for i in range(4):
            self.feed(t + i * 30, ERR)
        self.run_for(t + 120, 20, 10, fan1=0)
        self.assertEqual(len(self.lines("fans: fan1 stopped")), 1)
        self.assertEqual(len(self.lines("miner: restarted")), 1)

    def test_judged_without_an_uptime(self):
        t, el = self.run_for(T0, 600, 4)
        for i in range(2):
            row = hashing(0, fan0=0)
            del row["elapsed"]
            self.feed(t + i * 30, row)
        self.assertEqual(len(self.lines("fans: fan0 stopped")), 1)

    def test_mhs_av_counts_when_the_20_second_rate_is_missing(self):
        t, el = self.run_for(T0, 600, 4)
        for i in range(2):
            row = ok(el + i * 30, fan0=0)
            row["mhs_av"] = 680000.0
            self.feed(t + i * 30, row)
        self.assertEqual(len(self.lines("fans: fan0 stopped")), 1)

    def test_a_stopped_fan_is_not_also_high_and_does_not_skew_the_baseline(self):
        t, el = self.settle()
        for i in range(120):
            row = ok(el + i * 30, fan1=0)
            row["mhs_20s"] = 680000.0
            self.feed(t + i * 30, row)
        self.assertEqual([l[20:26] for l in self.lines("fans:")], ["fans: "])
        self.assertEqual(self.lines("fans: high"), [])

    def test_a_malformed_row_does_not_raise(self):
        self.feed(T0, {"http": "ok", "elapsed": 600, "fan0": "x", "fan1": None, "mhs_20s": "y"})
        self.assertEqual(self.lines("fans:"), [])


class HelpersTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(markers.duration(45), "45 s")
        self.assertEqual(markers.duration(420), "7 min")
        self.assertEqual(markers.duration(47071), "13 h 4 min")

    def test_event_log_counts_and_keeps_recent_lines(self):
        ev = EventLog()
        for i in range(250):
            ev.write("service: %d" % i)
        self.assertEqual(ev.written, 250)
        self.assertEqual(len(ev.recent), 200)
        self.assertTrue(ev.recent[-1].endswith("service: 249"))

    def test_recent_since_hands_out_each_line_once(self):
        ev = EventLog()
        ev.write("a")
        lines, seen = ev.recent_since(0)
        self.assertEqual([l[20:] for l in lines], ["a"])
        ev.write("b")
        ev.write("c")
        lines, seen = ev.recent_since(seen)
        self.assertEqual([l[20:] for l in lines], ["b", "c"])
        self.assertEqual(ev.recent_since(seen), ([], 3))
        for i in range(300):
            ev.write(str(i))
        lines, seen = ev.recent_since(seen)
        self.assertEqual((len(lines), seen), (200, 303))            # more than `recent` holds: the newest 200


class PollerWiringTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def test_same_uptime_two_hours_later_reads_as_a_restart(self):
        # the fixture's uptime never moves, so a clock two hours on puts the process start two hours later
        now = [T0]
        ev = EventLog()
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, events=ev, clock=lambda: now[0])
        p.poll_once()
        now[0] += 7200
        p.poll_once()
        self.assertEqual(len([l for l in ev.recent if " miner: restarted " in l]), 1)

    def test_a_marker_failure_costs_no_sample_and_logs_once(self):
        ev = EventLog()
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, events=ev)
        def boom(*a):
            raise ValueError("bad")
        p.markers.observe = boom
        p.poll_once()
        p.poll_once()
        self.assertEqual(p.samples, 2)
        self.assertEqual(len([l for l in ev.recent if "event markers failed: ValueError: bad" in l]), 1)

    def test_an_unwritable_event_log_does_not_stop_the_poller(self):
        # 0.10.2 review: the failure handler's own write raised and ended the poller thread
        ev = EventLog()
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, events=ev)
        def boom(*a):
            raise PermissionError(13, "denied")
        p.markers.observe = boom
        ev.write = boom
        row = p.poll_once()                 # raised PermissionError out of poll_once before the fix
        self.assertIn("http", row)
        self.assertTrue(p._markers_failed)


if __name__ == "__main__":
    unittest.main()
