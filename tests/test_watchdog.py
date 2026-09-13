"""Watchdog rules with a fake clock: stall, unreachable, settle gap, cap, sample gaps."""
import unittest

from gbox.config import DEFAULT_POWER
from gbox.events import EventLog
from gbox.plug import Plug, PlugError
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

    def test_external_restart_starts_settle_gap_without_counting(self):
        # someone pressed the dashboard's restart button: the miner is rebooting, so the
        # frozen counter that follows is expected and must not trigger a second restart
        self.feed(6, accepted=lambda i: i)
        self.wd.external_restart()
        self.assertIsNone(self.feed(20, accepted=500))
        self.assertEqual(self.restart.restarts, 0)
        self.assertEqual(self.wd.restarts_today(), 0)
        # after the gap, a full frozen window is judged again
        self.feed(10, accepted=500)
        self.assertEqual(self.restart.restarts, 1)


class FakePlugObject(Plug):
    """An in-memory plug: no sockets, a call log, knobs for every refusal path."""

    def __init__(self, device_id="plug-1", relay=True, watts=34.0, meter=True):
        self.device_id, self.relay, self.watts_value, self.meter = device_id, relay, watts, meter
        self.calls = []
        self.fail_identify = False
        self.fail_off = False

    def identify(self):
        if self.fail_identify:
            raise PlugError("plug did not answer")
        return {"model": "HS110(US)", "alias": "test", "device_id": self.device_id, "meter": self.meter, "hw": "1.0", "fw": "t"}

    def state(self):
        if self.fail_identify:
            raise PlugError("plug did not answer")
        return self.relay

    def watts(self):
        return self.watts_value if self.meter else None

    def off(self):
        self.calls.append("off")
        if self.fail_off:
            raise PlugError("plug did not answer")
        self.relay = False

    def on(self):
        self.calls.append("on")
        self.relay = True


class PowerCycleTest(unittest.TestCase):
    """The power rung: only on the frozen-controller signature, only after soft restarts failed, only the right plug."""

    INTERVAL = 30

    def setUp(self):
        self.clock = FakeClock()
        self.restart = Recorder()
        self.restart.fail = True                     # a frozen controller cannot take a soft restart
        self.lines = []
        self.events = EventLog()
        self.events.write = lambda m: self.lines.append(m)
        self.plug = FakePlugObject()
        self.power = dict(DEFAULT_POWER, host="x", device_id="plug-1", cycle=True)
        self.wd = self.make()

    def make(self, plug="default", **power):
        p = dict(self.power, **power)
        return Watchdog(self.restart, self.events, self.INTERVAL, stall_minutes=5, unreachable_minutes=2,
                        min_gap_minutes=10, max_restarts_per_day=6, clock=self.clock,
                        plug=self.plug if plug == "default" else plug, power=p, sleep=lambda s: None)

    def feed(self, n, ok=True, accepted=None, wd=None):
        wd = wd or self.wd
        last = None
        for i in range(n):
            self.clock.tick(self.INTERVAL)
            acc = accepted(i) if callable(accepted) else accepted
            wd.observe(ok, acc)
            last = wd.check()
        return last

    def freeze(self, minutes, wd=None):
        """A healthy window, then the miner off the network for `minutes`. Returns the time the episode began."""
        self.feed(10, accepted=lambda i: i, wd=wd)
        t0 = self.clock() + self.INTERVAL
        self.feed(round(minutes * 60 / self.INTERVAL), ok=False, wd=wd)
        return t0

    def power_lines(self):
        return [l for l in self.lines if l.startswith("power:")]

    def test_no_plug_never_cycles(self):
        wd = self.make(plug=None)
        self.freeze(60, wd=wd)
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(self.power_lines(), [])

    def test_cycles_once_after_two_failed_restarts_and_the_delay(self):
        t0 = self.freeze(14)
        self.assertEqual(self.plug.calls, [])                       # one failed restart so far, under 15 min
        self.feed(10, ok=False)                                     # 19 min: the second restart fails
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertGreaterEqual(self.clock() - t0, 15 * 60)
        self.assertEqual(self.wd.cycles_today(), 1)
        cycled = [l for l in self.power_lines() if l.startswith("power: cycled")]
        self.assertEqual(len(cycled), 1)
        self.assertIn("34 W before", cycled[0])
        self.assertIn("unreachable", cycled[0])

    def test_dry_run_logs_once_and_never_switches(self):
        wd = self.make(cycle=False)
        self.freeze(60, wd=wd)
        self.assertEqual(self.plug.calls, [])
        would = [l for l in self.power_lines() if "would cycle" in l]
        self.assertEqual(len(would), 1)
        self.assertIn("34 W", would[0])
        self.assertEqual(wd.cycles_today(), 0)

    def test_stalled_but_reachable_miner_gets_soft_restarts_only(self):
        self.restart.fail = False
        self.feed(10, accepted=lambda i: i)
        self.feed(40, accepted=500)                                 # frozen counter, HTTP fine
        self.assertGreaterEqual(self.restart.restarts, 2)
        self.assertEqual(self.plug.calls, [])

    def test_accepted_soft_restart_does_not_count_as_failed(self):
        self.restart.fail = False                                   # the PUT is accepted although polls fail
        self.freeze(60)
        self.assertEqual(self.plug.calls, [])

    def test_wrong_device_id_refuses_and_logs_once(self):
        wd = self.make(device_id="some-other-plug")
        self.freeze(60, wd=wd)
        self.assertEqual(self.plug.calls, [])
        refusals = [l for l in self.power_lines() if "not the configured device" in l]
        self.assertEqual(len(refusals), 1)

    def test_relay_already_off_refuses_and_logs_once(self):
        self.plug.relay = False
        self.freeze(60)
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(sum("is off" in l for l in self.power_lines()), 1)

    def test_plug_not_answering_refuses_and_logs_once(self):
        self.plug.fail_identify = True
        self.freeze(60)
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(sum("did not answer" in l for l in self.power_lines()), 1)
        self.assertGreaterEqual(sum("restart attempt failed" in l for l in self.lines), 2)   # soft path untouched

    def test_cap_per_day(self):
        wd = self.make(max_cycles_per_day=1)
        self.freeze(20, wd=wd)
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.feed(10, accepted=lambda i: i, wd=wd)                  # the miner came back
        self.clock.tick(25 * 60)                                    # past the settle gap
        self.freeze(40, wd=wd)
        self.assertEqual(self.plug.calls, ["off", "on"])            # no second cycle
        self.assertEqual(sum("cap" in l for l in self.power_lines()), 1)

    def test_settle_gap_after_a_cycle_then_judged_again(self):
        self.freeze(20)
        self.assertEqual(self.plug.calls, ["off", "on"])
        restarts_before = sum("restart attempt failed" in l for l in self.lines)
        self.feed(38, ok=False)                                     # 19 min inside the 20-min settle gap
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(sum("restart attempt failed" in l for l in self.lines), restarts_before)
        self.feed(30, ok=False)                                     # 34 min: gap over, window, first fresh restart failed
        self.assertEqual(self.plug.calls, ["off", "on"])            # the ladder needs two fresh failed restarts
        self.assertEqual(self.wd.episode_failed_restarts, 1)
        self.feed(20, ok=False)                                     # 44 min: gap, window, second failed restart, cycle
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])
        self.assertEqual(self.wd.cycles_today(), 2)

    def test_on_still_attempted_when_off_failed(self):
        self.plug.fail_off = True
        self.freeze(20)
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(sum("cycle failed" in l for l in self.power_lines()), 1)

    def test_ok_sample_resets_the_failed_restart_count(self):
        self.freeze(5)                                              # one failed restart
        self.feed(10, accepted=lambda i: i)                         # back, briefly
        self.clock.tick(15 * 60)
        self.freeze(5)                                              # a new episode: one failed restart again
        self.assertEqual(self.plug.calls, [])                       # two in total, but not two in this episode

    def test_no_meter_says_so_in_the_line(self):
        self.plug.meter = False
        self.freeze(20)
        cycled = [l for l in self.power_lines() if l.startswith("power: cycled")]
        self.assertEqual(len(cycled), 1)
        self.assertIn("no meter", cycled[0])

    def test_health_fields(self):
        self.assertEqual(self.wd.cycles_today(), 0)
        self.assertIsNone(self.wd.last_power_reason)
        self.freeze(20)
        self.assertEqual(self.wd.cycles_today(), 1)
        self.assertIn("unreachable", self.wd.last_power_reason)


if __name__ == "__main__":
    unittest.main()


class SeedFromEventsTest(unittest.TestCase):
    """The daily caps survive a service restart: the watchdog reads its own lines back from the event log."""
    INTERVAL = 30
    T0 = 1_700_000_000.0        # a fixed "now"; event lines are stamped in local time, so build them from it

    @staticmethod
    def stamp(t):
        import datetime
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")

    def setUp(self):
        self.clock = FakeClock(self.T0)
        self.restart = Recorder()
        self.events = EventLog()
        self.plug = FakePlugObject()
        self.power = dict(DEFAULT_POWER, host="x", device_id="plug-1", cycle=True, max_cycles_per_day=3)
        self.wd = Watchdog(self.restart, self.events, self.INTERVAL, stall_minutes=5, unreachable_minutes=2,
                           min_gap_minutes=10, max_restarts_per_day=6, clock=self.clock,
                           plug=self.plug, power=self.power, sleep=lambda s: None)
        self.wrote = []
        self.events.write = lambda m: self.wrote.append(m) or m

    def lines(self, *items):
        return ["%s %s\n" % (self.stamp(self.T0 - ago), text) for ago, text in items]

    def test_counts_only_recent_watchdog_restarts_and_cycles(self):
        n = self.wd.seed_from_events(self.lines(
            (90000, "watchdog: restart #1 sent (miner unreachable for 2 min)"),      # 25 h ago: out
            (7200, "watchdog: restart #2 sent (accepted shares frozen for 5 min)"),
            (3600, "watchdog: restart attempt failed: PUT mcb/restart: timed out (miner unreachable for 2 min)"),
            (3000, "power: cycled #1 today: off 15 s, on (miner unreachable for 2 min; 43 W before)"),
            (2000, "dashboard: power: cycled by hand (gbox power cycle; 187 W before)"),   # not the watchdog's
            (1000, "power: would cycle now (miner unreachable for 2 min; 40 W before); dry run"),
            (500, "service: started v0.4.0, miner x, poll 30s, watchdog on, power plug armed, listening on 127.0.0.1:8765"),
        ))
        self.assertEqual(n, (2, 1))
        self.assertEqual(self.wd.restarts_today(), 2)
        self.assertEqual(self.wd.cycles_today(), 1)
        self.assertEqual(len(self.wrote), 1)
        self.assertIn("2 restarts and 1 cycle", self.wrote[0])

    def test_failed_attempts_count_against_the_restart_cap_as_they_do_live(self):
        # check() appends to _restart_times before calling restart(), so a timed-out attempt used a slot
        self.wd.seed_from_events(self.lines(
            (3600, "watchdog: restart attempt failed: PUT mcb/restart: timed out (miner unreachable for 2 min)")))
        self.assertEqual(self.wd.restarts_today(), 1)

    def test_nothing_to_seed_writes_nothing(self):
        self.assertEqual(self.wd.seed_from_events(self.lines((10, "service: started v0.4.0, x"))), (0, 0))
        self.assertEqual(self.wrote, [])

    def test_garbage_lines_are_ignored(self):
        self.assertEqual(self.wd.seed_from_events(["", "not a line\n", "2026-13-99 25:61:61 power: cycled #1 today: x\n"]), (0, 0))

    def test_newest_seeded_restart_holds_the_settle_gap(self):
        self.wd.seed_from_events(self.lines((120, "watchdog: restart #1 sent (miner unreachable for 2 min)")))
        self.assertAlmostEqual(self.wd.last_restart, self.T0 - 120, delta=1)
        self.wd.seed_from_events(self.lines((240, "power: cycled #1 today: off 15 s, on (x; y)")))
        self.assertAlmostEqual(self.wd.power_gap_until, self.T0 - 240 + self.power["settle_minutes"] * 60, delta=1)

    def test_three_seeded_cycles_refuse_a_fourth_and_log_the_cap(self):
        """The done-when: a service restarted onto a hung miner does not get three fresh cycles."""
        self.wd.seed_from_events(self.lines(
            (20000, "watchdog: restart attempt failed: e (miner unreachable for 2 min)"),
            (19400, "watchdog: restart attempt failed: e (miner unreachable for 2 min)"),
            (19000, "power: cycled #1 today: off 15 s, on (x; y)"),
            (10000, "power: cycled #2 today: off 15 s, on (x; y)"),
            (5000, "power: cycled #3 today: off 15 s, on (x; y)"),
        ))
        self.wrote.clear()
        self.restart.fail = True
        for _ in range(60):                 # 30 min of the miner off the network, restarts timing out
            self.clock.tick(self.INTERVAL)
            self.wd.observe(False, None)
            self.wd.check()
        self.assertNotIn("off", self.plug.calls)
        self.assertTrue(any("3 cycles in 24 h is the cap; not cycling" in m for m in self.wrote), self.wrote)

    def test_seed_restores_a_running_hold(self):
        self.wd.seed_from_events(self.lines((600, "hold: started by you until %s (PSU swap)" % self.stamp(self.T0 + 3000))))
        self.assertIsNotNone(self.wd.hold)
        self.assertEqual(self.wd.hold["reason"], "PSU swap")
        self.assertAlmostEqual(self.wd.hold["until"], self.T0 + 3000, delta=1)
        self.assertTrue(any("hold" in m and "picked up" in m for m in self.wrote), self.wrote)

    def test_seed_ignores_a_released_or_expired_hold(self):
        self.wd.seed_from_events(self.lines(
            (600, "hold: started by you until %s (PSU swap)" % self.stamp(self.T0 + 3000)),
            (300, "hold: released by you")))
        self.assertIsNone(self.wd.hold)
        self.wd.seed_from_events(self.lines((600, "hold: started by you until %s (old)" % self.stamp(self.T0 - 100))))
        self.assertIsNone(self.wd.hold)

    def test_seed_restores_a_no_expiry_hold(self):
        self.wd.seed_from_events(self.lines((600, "hold: started by you, no expiry (switched off)")))
        self.assertIsNotNone(self.wd.hold)
        self.assertIsNone(self.wd.hold["until"])
        self.assertEqual(self.wd.hold["reason"], "switched off")


class HoldTest(unittest.TestCase):
    """A hold: the miner is expected to be unreachable, so nothing is judged until it is back."""
    INTERVAL = 30

    def setUp(self):
        self.clock = FakeClock()
        self.restart = Recorder()
        self.lines = []
        self.events = EventLog()
        self.events.write = lambda m: self.lines.append(m) or m
        self.wd = Watchdog(self.restart, self.events, self.INTERVAL, stall_minutes=5, unreachable_minutes=2,
                           min_gap_minutes=10, max_restarts_per_day=6, clock=self.clock)

    def feed(self, n, ok=True, accepted=None):
        last = None
        for i in range(n):
            self.clock.tick(self.INTERVAL)
            acc = accepted(i) if callable(accepted) else accepted
            self.wd.observe(ok, acc)
            last = self.wd.check()
        return last

    def hold_lines(self):
        return [l for l in self.lines if l.startswith("hold:")]

    def test_hold_stops_judging_until_released(self):
        self.feed(6, accepted=lambda i: i)
        self.wd.hold_start(60, "PSU swap")
        self.assertIsNone(self.feed(10, ok=False))
        self.assertEqual(self.restart.restarts, 0)
        self.wd.hold_release("you")
        self.assertIsNone(self.wd.hold)
        self.assertEqual(self.hold_lines()[-1], "hold: released by you")
        self.feed(10, ok=False)                                  # a full fresh window after the release
        self.assertEqual(self.restart.restarts, 1)

    def test_two_good_samples_release_the_hold(self):
        self.wd.hold_start(60, "PSU swap")
        self.feed(3, ok=False)
        self.feed(1, accepted=1)
        self.assertIsNotNone(self.wd.hold)
        self.feed(1, accepted=2)
        self.assertIsNone(self.wd.hold)
        self.assertIn("hold: released, miner back after 2 min", self.hold_lines()[-1])

    def test_one_good_sample_between_failures_does_not_release(self):
        self.wd.hold_start(60, "PSU swap")
        self.feed(1, accepted=1)
        self.feed(1, ok=False)
        self.feed(1, accepted=2)
        self.assertIsNotNone(self.wd.hold)
        self.assertEqual(self.wd.hold["ok_streak"], 1)

    def test_expiry_with_the_miner_down_resumes_the_watchdog(self):
        self.feed(6, accepted=lambda i: i)
        self.wd.hold_start(20, "cable")
        self.feed(41, ok=False)                                  # 20.5 min
        self.assertIsNone(self.wd.hold)
        self.assertIn("hold: expired after 20 min with the miner still unreachable; watchdog resumed", self.hold_lines()[-1])
        self.assertEqual(self.restart.restarts, 0)               # the window was cleared on release
        self.feed(10, ok=False)                                  # a full fresh window is needed again
        self.assertEqual(self.restart.restarts, 1)

    def test_no_expiry_hold_never_expires(self):
        self.feed(6, accepted=lambda i: i)
        self.wd.hold_start(None, "switched off")
        self.assertIsNone(self.feed(360, ok=False))              # three hours
        self.assertIsNotNone(self.wd.hold)
        self.assertEqual(self.restart.restarts, 0)

    def test_hold_lines_and_info(self):
        import datetime
        h = self.wd.hold_start(60, "PSU swap")
        until = datetime.datetime.fromtimestamp(self.clock() + 3600).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.hold_lines()[-1], "hold: started by you until %s (PSU swap)" % until)
        self.assertEqual(h["until"], self.clock() + 3600)
        info = self.wd.hold_info()
        self.assertEqual(info["minutes_left"], 60)
        self.assertEqual(info["until"], until)
        self.assertEqual(info["reason"], "PSU swap")
        self.assertEqual(info["source"], "page")
        self.wd.hold_start(None, "switched off", source="cli")
        self.assertEqual(self.hold_lines()[-1], "hold: started by you, no expiry (switched off)")
        self.assertIsNone(self.wd.hold_info()["minutes_left"])
        self.wd.hold_start(20, "switched on, booting", source="schedule")
        self.assertTrue(self.hold_lines()[-1].startswith("hold: started by the schedule until "))
        self.assertIsNone(Watchdog(self.restart, self.events, 30, clock=self.clock).hold_info())
