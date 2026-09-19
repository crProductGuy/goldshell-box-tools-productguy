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

    def test_unreachable_is_judged_again_as_soon_as_the_gap_ends(self):
        """The second rung: after the settle gap the unreachable rule needs only its own window, which
        may reach back into the gap. Until 2026-09-13 it waited for a full stall window of fresh samples
        as well, so the second restart landed about 10 min after the first instead of 5 (09-12 18:17 to 18:27)."""
        self.feed(6, accepted=lambda i: i)
        self.feed(4, ok=False)
        self.assertEqual(self.restart.restarts, 1)
        first = self.wd.last_restart
        self.assertIsNone(self.feed(20, ok=False))              # the 10-minute gap: silence
        self.assertEqual(self.restart.restarts, 1)
        reason = self.feed(1, ok=False)                         # first sample after the gap: still dark
        self.assertEqual(self.restart.restarts, 2)
        self.assertIn("unreachable", reason or "")
        self.assertEqual(self.wd.last_restart - first, 21 * self.INTERVAL)

    def test_stall_still_needs_a_full_fresh_window_after_the_gap(self):
        """A reachable miner with a frozen counter is judged on stall_minutes of samples after the gap, as before."""
        self.feed(10, accepted=500)
        self.assertEqual(self.restart.restarts, 1)
        self.assertIsNone(self.feed(29, accepted=500))          # gap (20) plus 9 of the 10-row window
        self.feed(1, accepted=500)
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
        self.identifies = 0
        self.fail_identify = False
        self.fail_off = False

    def identify(self):
        self.identifies += 1
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
        """unreachable 2, min_gap 10, after 5 (the default): restart #1 at 1.5 min, #2 at 12 min on the first
        sample after the gap, and the cycle with it since the episode is past after_minutes."""
        t0 = self.freeze(12)                                        # samples up to 11.5 min: the gap's last
        self.assertEqual(self.plug.calls, [])                       # one failed restart so far
        self.assertEqual(self.wd.episode_failed_restarts, 1)
        self.feed(1, ok=False)                                      # 12 min: the second restart fails
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(self.clock() - t0, 12 * 60)
        self.assertGreaterEqual(self.clock() - t0, DEFAULT_POWER["after_minutes"] * 60)
        self.assertEqual(self.wd.cycles_today(), 1)
        cycled = [l for l in self.power_lines() if l.startswith("power: cycled")]
        self.assertEqual(len(cycled), 1)
        self.assertIn("34 W before", cycled[0])
        self.assertIn("unreachable", cycled[0])

    def test_ladder_reaches_the_plug_at_about_seven_minutes_with_the_live_timings(self):
        """unreachable 2, min_gap 5, after 5 (the defaults since 2026-09-12): restart #1 at 1.5 min into a
        freeze, the gap to 6.5 min, restart #2 and the cycle on the next sample, at 7 min."""
        wd = Watchdog(self.restart, self.events, self.INTERVAL, stall_minutes=5, unreachable_minutes=2,
                      min_gap_minutes=5, max_restarts_per_day=20, clock=self.clock,
                      plug=self.plug, power=dict(self.power, after_minutes=5, max_cycles_per_day=8),
                      sleep=lambda s: None)
        t0 = self.freeze(6.5, wd=wd)                                # samples up to 6 min
        self.feed(1, ok=False, wd=wd)                               # 6.5 min: the gap's last sample
        self.assertEqual(self.plug.calls, [])
        self.assertEqual(wd.restarts_today(), 1)
        self.feed(1, ok=False, wd=wd)                               # 7 min: the first sample after the gap
        self.assertEqual(wd.restarts_today(), 2)
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(self.clock() - t0, 7 * 60)

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
        """unreachable 2, min_gap 10, after 15 here: restarts at 1.5 and 12 min, the cycle at 15 when the episode
        is old enough, settle to 35; then the whole ladder again (35.5 and 46 min) before a second cycle.

        settle_minutes is pinned at 20 rather than taken from the default, which became 6 on 2026-09-17. What
        this test is about is the gap's mechanics -- nothing judged inside it, the full ladder again after it --
        so it states the gap length it exercises instead of inheriting a tunable number."""
        self.wd = self.make(after_minutes=15, settle_minutes=20)
        self.freeze(20)                                             # samples up to 19.5 min
        self.assertEqual(self.plug.calls, ["off", "on"])
        restarts_before = sum("restart attempt failed" in l for l in self.lines)
        self.feed(31, ok=False)                                     # 35 min: the settle gap's last sample
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(sum("restart attempt failed" in l for l in self.lines), restarts_before)
        self.feed(1, ok=False)                                      # 35.5 min: gap over, first fresh restart failed
        self.assertEqual(self.plug.calls, ["off", "on"])            # the ladder needs two fresh failed restarts
        self.assertEqual(self.wd.episode_failed_restarts, 1)
        self.assertEqual(sum("restart attempt failed" in l for l in self.lines), restarts_before + 1)
        self.feed(20, ok=False)                                     # 45.5 min: the gap's last sample
        self.assertEqual(self.wd.episode_failed_restarts, 1)
        self.feed(1, ok=False)                                      # 46 min: second failed restart, cycle
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])
        self.assertEqual(self.wd.cycles_today(), 2)

    def test_a_cycle_that_leaves_the_wall_dark_is_repeated_once_at_once(self):
        """2026-09-15 06:40: the plug cycled, the controller never came up (12 W for 25 min), and the ladder waited
        out its settle gap before the cycle that worked. Now: under boot_watts two minutes after a cycle, cycle
        again at once; if that one fails too, say so and leave it to the ladder."""
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: two failed restarts, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 12.0                                # the controller did not come up
        self.feed(3, ok=False)                                      # 13.5 min: the check is not due yet
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.feed(1, ok=False)                                      # 14 min: two minutes after the cycle
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])
        self.assertEqual(self.wd.cycles_today(), 2)
        line = [l for l in self.power_lines() if l.startswith("power: cycled #2")][0]
        self.assertIn("controller did not come up after cycle #1: 12 W after 2 min", line)
        self.plug.watts_value = 12.0                                # still dark after the repeat
        self.feed(4, ok=False)                                      # 16 min: the second check
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])          # no third cycle from the check
        self.assertTrue(any("already cycled again once" in l for l in self.power_lines()))

    def test_a_cycle_that_boots_passes_the_check_quietly(self):
        self.freeze(12)
        self.feed(1, ok=False)                                      # cycle #1
        self.plug.watts_value = 36.0                                # the controller is up, the board not yet hashing
        self.feed(4, ok=False)                                      # 14 min: the check passes
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertFalse(any("did not come up" in l for l in self.lines))
        # a good sample before the check clears it
        self.wd = self.make()
        self.plug.calls.clear()
        self.freeze(12)
        self.feed(1, ok=False)
        self.plug.watts_value = 12.0
        self.feed(1, ok=True)                                       # it answered: no check, whatever the meter says
        self.feed(3, ok=True)
        self.assertEqual(self.plug.calls, ["off", "on"])

    def test_the_boot_check_honours_the_daily_cap_and_can_be_switched_off(self):
        self.wd = self.make(max_cycles_per_day=1)
        self.freeze(12)
        self.feed(1, ok=False)                                      # cycle #1, the cap
        self.plug.watts_value = 12.0
        self.feed(4, ok=False)
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertTrue(any("is the cap; not cycling" in l and "did not come up" in l for l in self.power_lines()))
        self.wd = self.make(boot_check_minutes=0)
        self.plug.calls.clear()
        self.freeze(12)
        self.feed(1, ok=False)
        self.plug.watts_value = 12.0
        self.feed(10, ok=False)
        self.assertEqual(self.plug.calls, ["off", "on"])

    def test_a_refused_rung_is_not_retried_on_every_sample(self):
        """The plug is asked once per rung, not every 30 s for the rest of the outage: after a refusal the
        next try waits for the next restart's turn (12 min: refused; 22.5 min: asked again)."""
        self.plug.fail_identify = True
        self.freeze(12.5)                                           # restarts at 1.5 and 12 min: the first try
        self.assertEqual(self.plug.identifies, 1)
        self.feed(20, ok=False)                                     # 22 min: the gap's last sample
        self.assertEqual(self.plug.identifies, 1)
        self.feed(1, ok=False)                                      # 22.5 min: restart #3, the second try
        self.assertEqual(self.plug.identifies, 2)
        self.assertEqual(sum("did not answer" in l for l in self.power_lines()), 1)

    def test_after_minutes_is_honoured_on_any_dark_sample_once_two_restarts_failed(self):
        """after_minutes longer than unreachable plus the gap: the plug moves when the age is reached (15 min),
        not a whole gap later at the next restart's turn."""
        self.wd = self.make(after_minutes=15)
        t0 = self.freeze(12.5)                                      # restarts at 1.5 and 12 min, two failed
        self.assertEqual(self.wd.episode_failed_restarts, 2)
        self.assertEqual(self.plug.calls, [])
        self.feed(5, ok=False)                                      # 14.5 min
        self.assertEqual(self.plug.calls, [])
        self.feed(1, ok=False)                                      # 15 min
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(self.clock() - t0, 15 * 60)

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

    # -------------------------------------------------- task A: observe() must gate the boot check on hashing

    def test_a1_boot_check_fires_when_the_box_stays_fully_dark(self):
        """Regression guard: a fully dark box (ok=False) after a cycle must still get the repeat cycle. This
        passed before the task A fix and must keep passing after it."""
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: second failed restart, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 12.0                                # under boot_watts (20): never came up
        self.feed(4, ok=False)                                      # 14 min: the boot check is due
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])
        self.assertTrue(any("did not come up" in l for l in self.power_lines()))

    def test_a2_boot_check_fires_after_an_http_only_cold_start(self):
        """The defect (2026-09-18): the controller boots and answers HTTP (ok=True) while the hashboard stays
        dead (hashing=False) at a low draw. Before the observe() fix this cleared _boot_check on any ok=True
        sample regardless of hashing, so the check never fired. Fails before the fix, passes after."""
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: second failed restart, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 3.0                                 # under boot_watts (20): hashboard dead
        for _ in range(4):                                          # 14 min: HTTP answers ok, hashboard silent
            self.clock.tick(self.INTERVAL)
            self.wd.observe(True, None, hashing=False)
            self.wd.check()
        self.assertEqual(self.plug.calls, ["off", "on", "off", "on"])
        self.assertTrue(any("did not come up" in l for l in self.power_lines()))

    def test_a3_healthy_boot_clears_the_check_without_a_repeat_cycle(self):
        """Guard against overcorrecting task A into a restart loop on a healthy box: ok=True with hashing=True
        at a normal draw must clear the boot check and fire no repeat cycle."""
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: second failed restart, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 180.0                               # hashing normally, well over boot_watts
        for _ in range(4):
            self.clock.tick(self.INTERVAL)
            self.wd.observe(True, None, hashing=True)
            self.wd.check()
        self.assertEqual(self.plug.calls, ["off", "on"])            # no repeat cycle
        self.assertFalse(any("did not come up" in l for l in self.lines))

    # ---------------------------------------- the two branches of _check_boot that had no test (2026-09-19)

    def test_b1_a_meterless_plug_says_so_instead_of_passing_the_check_silently(self):
        """`w is None` returned with no event line, so a plug that cannot measure watts looked exactly like a
        box that booted fine. The check is impossible here, and the log now says which of the two it was."""
        self.plug.meter = False
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: second failed restart, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 3.0                                 # would be under boot_watts, but unreadable
        self.feed(4, ok=False)                                      # 14 min: the boot check is due
        self.assertEqual(self.plug.calls, ["off", "on"])            # never cycle on a reading we do not have
        self.assertTrue(any("has no meter" in l for l in self.power_lines()))
        self.assertFalse(any("did not come up" in l for l in self.power_lines()))

    def test_b2_a_plug_that_stops_answering_at_the_boot_check_is_logged_not_swallowed(self):
        """`identify()` raising inside the check is caught and logged, and must not cycle: an unreachable plug
        is not evidence about the miner."""
        self.freeze(12)
        self.feed(1, ok=False)                                      # 12 min: second failed restart, cycle #1
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.plug.watts_value = 3.0
        self.plug.fail_identify = True                              # the plug drops off before the check is due
        self.feed(4, ok=False)                                      # 14 min: the boot check is due
        self.assertEqual(self.plug.calls, ["off", "on"])            # no cycle on no evidence
        self.assertTrue(any("plug did not answer" in l for l in self.power_lines()))
        self.assertFalse(any("did not come up" in l for l in self.power_lines()))


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

    # -------------------------------------------------- task B1: SEED_RE must match both wordings

    def test_seed_re_counts_both_the_old_today_wording_and_the_new_in_24h_wording(self):
        """The written wording changed from "today" to "in 24 h" (task B1); SEED_RE must still match the old
        form in already-written log lines as well as the new form going forward, or seeding silently drops
        every historical cycle line."""
        n = self.wd.seed_from_events(self.lines(
            (3000, "power: cycled #1 today: off 15 s, on (miner unreachable for 2 min; 43 W before)"),
            (2000, "power: cycled #2 in 24 h: off 15 s, on (miner unreachable for 2 min; 40 W before)"),
        ))
        self.assertEqual(n, (0, 2))
        self.assertEqual(self.wd.cycles_today(), 2)

    # -------------------------------------------------- task B2: a second seed must not double-count

    def test_seeding_the_same_tail_twice_does_not_double_count(self):
        """Measured: before this fix, seeding the same two-cycle tail twice gave cycles_today() == 4."""
        lines = self.lines(
            (19000, "power: cycled #1 today: off 15 s, on (x; y)"),
            (10000, "power: cycled #2 today: off 15 s, on (x; y)"),
        )
        first = self.wd.seed_from_events(lines)
        restarts_after_first, cycles_after_first = self.wd.restarts_today(), self.wd.cycles_today()
        wrote_after_first = len(self.wrote)
        second = self.wd.seed_from_events(lines)
        self.assertEqual(first, (0, 2))
        self.assertEqual(second, (0, 0))                            # nothing new: no double-count
        self.assertEqual(self.wd.restarts_today(), restarts_after_first)
        self.assertEqual(self.wd.cycles_today(), cycles_after_first)
        self.assertEqual(len(self.wrote), wrote_after_first)        # the pickup line is not written again

    def test_a_live_cycles_own_log_line_is_not_counted_a_second_time(self):
        """Review finding, 2026-09-18. The dedup only knew stamps seeded from the log, so a cycle this process
        performed was counted again if its own line came back through a later seed: one slot of
        max_cycles_per_day burned, and a needed cycle refused. Latent (the one call site seeds at service
        start, before anything live), so this pins it rather than reporting a live defect."""
        self.wd._cycle("test reason", "34 W before")
        own_line = "%s %s\n" % (self.stamp(self.clock.t),
                                [m for m in self.wrote if "cycled" in m][-1])
        self.assertEqual(self.wd.cycles_today(), 1)
        self.assertEqual(self.wd.seed_from_events([own_line]), (0, 0))
        self.assertEqual(self.wd.cycles_today(), 1)

    def test_a_seeded_line_older_than_a_live_one_still_ages_out(self):
        """Review finding, 2026-09-18. cycles_today() prunes with popleft and stops at the first entry inside
        the window, so an older seeded entry appended after a newer live one was never pruned and inflated the
        count for good. seed_from_events now re-sorts instead of appending."""
        self.wd._cycle("test reason", "34 W before")                # live, at T0
        self.wd.seed_from_events(self.lines((80000, "power: cycled #1 in 24 h: off 15 s, on (x; y)")))
        self.assertEqual(list(self.wd._cycle_times), sorted(self.wd._cycle_times))
        self.assertEqual(self.wd.cycles_today(), 2)
        self.clock.tick(5.5 * 3600)                                 # the 80000 s-old one is now outside 24 h
        self.assertEqual(self.wd.cycles_today(), 1)

    def test_seeding_twice_does_not_reset_a_restored_holds_ok_streak(self):
        """Review finding, 2026-09-18. _seed_hold sat outside the dedup, so a second seed replaced the hold
        object and reset ok_streak to 0, delaying a legitimate release by HOLD_OK_SAMPLES polls."""
        lines = self.lines((600, "hold: started by you until %s (PSU swap)" % self.stamp(self.T0 + 3000)))
        self.wd.seed_from_events(lines)
        self.assertIsNotNone(self.wd.hold)
        self.wd.observe(True, None, hashing=True)                   # one good sample towards the release
        self.assertEqual(self.wd.hold["ok_streak"], 1)
        wrote_before = len(self.wrote)
        self.wd.seed_from_events(lines)
        self.assertEqual(self.wd.hold["ok_streak"], 1)              # not reset back to 0
        self.assertEqual(len(self.wrote), wrote_before)             # and the pickup line is not written again


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
        self.assertIn("hold: released, miner hashing again after 2 min", self.hold_lines()[-1])

    def test_answering_without_hashing_does_not_release(self):
        """2026-09-13 15:46: the controller came back without its hashboard, answered HTTP with a zero hashrate,
        and the hold released on two answers. Release must mean hashing, not answering."""
        self.wd.hold_start(20, "switched on, booting")
        for i in range(4):
            self.clock.tick(self.INTERVAL)
            self.wd.observe(True, 0, hashing=False)
            self.wd.check()
        self.assertIsNotNone(self.wd.hold)
        self.assertEqual(self.wd.hold["ok_streak"], 0)
        self.clock.tick(self.INTERVAL); self.wd.observe(True, 1, hashing=True); self.wd.check()
        self.assertIsNotNone(self.wd.hold)
        self.clock.tick(self.INTERVAL); self.wd.observe(True, 2, hashing=True); self.wd.check()
        self.assertIsNone(self.wd.hold)
        self.assertIn("hold: released, miner hashing again after 3 min", self.hold_lines()[-1])

    def test_hashing_defaults_to_the_ok_flag(self):
        self.wd.hold_start(20, "x")
        self.feed(2, accepted=lambda i: i)          # observe() without the hashing argument: ok counts, as before
        self.assertIsNone(self.wd.hold)

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
