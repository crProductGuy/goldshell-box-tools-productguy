"""Watchdog rules with a fake clock: stall, unreachable, settle gap, cap, sample gaps."""
import unittest

from gbox.events import EventLog
from gbox.watchdog import Watchdog


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


class Recorder:
    def __init__(self):
        self.restarts = 0
        self.fail = False

    def __call__(self):
        if self.fail:
            raise RuntimeError("PUT mcb/restart: timed out")
        self.restarts += 1


class WatchdogTest(unittest.TestCase):
    INTERVAL = 30

    def setUp(self):
        self.clock = FakeClock()
        self.restart = Recorder()
        self.events = EventLog()            # no path: in-memory only
        self.wd = Watchdog(self.restart, self.events, self.INTERVAL, stall_minutes=5,
                           unreachable_minutes=2, min_gap_minutes=10, max_restarts_per_day=6,
                           clock=self.clock)

    def feed(self, n, ok=True, accepted=None, step=None):
        """n samples, one per interval. accepted may be a callable of the sample index."""
        last = None
        for i in range(n):
            self.clock.tick(step or self.INTERVAL)
            acc = accepted(i) if callable(accepted) else accepted
            self.wd.observe(ok, acc)
            last = self.wd.check()
        return last

    def test_healthy_miner_never_restarts(self):
        self.assertIsNone(self.feed(40, accepted=lambda i: 100 + i))
        self.assertEqual(self.restart.restarts, 0)

    def test_frozen_accepted_counter_restarts_after_window(self):
        self.assertIsNone(self.feed(9, accepted=500))
        self.assertIn("frozen", self.feed(1, accepted=500))
        self.assertEqual(self.restart.restarts, 1)

    def test_unreachable_restarts_after_two_minutes(self):
        self.feed(6, accepted=lambda i: i)          # healthy prefix so a full window exists
        self.assertIsNone(self.feed(3, ok=False))
        self.assertIn("unreachable", self.feed(1, ok=False))
        self.assertEqual(self.restart.restarts, 1)

    def test_settle_gap_after_restart(self):
        self.feed(10, accepted=500)
        self.assertEqual(self.restart.restarts, 1)
        # still frozen for the next 10 minutes: nothing, the miner is rebooting
        self.assertIsNone(self.feed(20, accepted=500))
        self.assertEqual(self.restart.restarts, 1)
        # a full window after the gap, still frozen: second restart
        self.feed(10, accepted=500)
        self.assertEqual(self.restart.restarts, 2)

    def test_cap_per_day_logs_once(self):
        lines = []
        self.events.write = lambda m: lines.append(m)
        for _ in range(6):
            self.feed(10, accepted=1)
            self.feed(20, accepted=1)
        self.assertEqual(self.restart.restarts, 6)
        self.feed(10, accepted=1)
        self.feed(10, accepted=1)
        self.assertEqual(self.restart.restarts, 6)
        self.assertEqual(sum("cap" in l for l in lines), 1)
        # the cap is a rolling day: 24 h later, restarts resume
        self.clock.tick(86400)
        self.feed(10, accepted=1)
        self.assertEqual(self.restart.restarts, 7)

    def test_gap_in_samples_is_not_judged(self):
        self.feed(5, accepted=500)
        self.clock.tick(3600)                        # the PC slept for an hour
        self.assertIsNone(self.feed(5, accepted=500))
        self.assertEqual(self.restart.restarts, 0)
        self.assertIsNotNone(self.feed(5, accepted=500))   # fresh contiguous window

    def test_stale_samples_are_not_judged(self):
        self.feed(10, accepted=lambda i: i)
        self.wd.observe(True, 999, self.clock() - 3600)   # a sample from an hour ago
        self.assertIsNone(self.wd.check())

    def test_none_accepted_does_not_count_as_frozen(self):
        self.assertIsNone(self.feed(12, accepted=None))

    def test_failed_restart_is_logged_and_gap_still_applies(self):
        lines = []
        self.events.write = lambda m: lines.append(m)
        self.restart.fail = True
        self.feed(10, accepted=1)
        self.assertTrue(any("failed" in l for l in lines))
        self.assertIsNone(self.feed(5, accepted=1))


if __name__ == "__main__":
    unittest.main()
