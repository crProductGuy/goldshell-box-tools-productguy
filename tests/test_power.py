"""PowerControl: off, on and cycle through the configured plug, each under a hold; the schedule's edges."""
import threading
import unittest

from gbox.config import DEFAULT_POWER
from gbox.events import EventLog
from gbox.power import PowerControl, PowerRefused
from gbox.watchdog import Watchdog
from tests.test_watchdog import FakeClock, FakePlugObject, Recorder


class PowerControlTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.lines = []
        self.events = EventLog()
        self.events.write = lambda m: self.lines.append(m) or m
        self.plug = FakePlugObject(watts=197.0)
        self.power = dict(DEFAULT_POWER, host="x", device_id="plug-1", settle_minutes=20, off_seconds=15)
        self.wd = Watchdog(Recorder(), self.events, 30, clock=self.clock, plug=self.plug, power=self.power)
        self.slept = []
        self.pc = PowerControl(self.plug, self.power, self.wd, self.events, sleep=self.slept.append, clock=self.clock)

    def power_lines(self):
        return [l for l in self.lines if l.startswith("power:")]

    def test_off_opens_the_relay_under_a_no_expiry_hold(self):
        st = self.pc.off("page")
        self.assertFalse(self.plug.relay)
        self.assertEqual(self.power_lines()[-1], "power: switched off by you (page; 197 W before)")
        self.assertIsNone(self.wd.hold["until"])
        self.assertEqual(self.wd.hold["reason"], "switched off")
        self.assertEqual(st["relay"], False)
        self.assertTrue(st["off_by_you"])
        self.assertIsNone(st["busy"])
        self.assertIsNone(st["hold"]["until"])

    def test_on_closes_the_relay_under_a_settle_hold(self):
        self.pc.off("page")
        st = self.pc.on("page")
        self.assertTrue(self.plug.relay)
        self.assertEqual(self.power_lines()[-1], "power: switched on by you (page)")
        self.assertAlmostEqual(self.wd.hold["until"], self.clock() + 20 * 60, delta=1)
        self.assertEqual(self.wd.hold["reason"], "switched on, booting")
        self.assertFalse(st["off_by_you"])

    def test_sources_name_who_did_it(self):
        self.pc.off("cli")
        self.assertEqual(self.power_lines()[-1], "power: switched off by you (gbox power off; 197 W before)")
        self.pc.on("schedule")
        self.assertEqual(self.power_lines()[-1], "power: switched on by the schedule")
        self.assertEqual(self.wd.hold["source"], "schedule")

    def test_cycle_answers_at_once_then_switches_off_and_on(self):
        st = self.pc.cycle("page")
        self.assertEqual(st["busy"], "cycling")
        self.assertEqual(self.wd.hold["reason"], "power cycle")
        self.pc.join(5)
        self.assertEqual(self.plug.calls, ["off", "on"])
        self.assertEqual(self.slept, [15])
        self.assertTrue(self.plug.relay)
        self.assertEqual(self.power_lines()[-1], "power: cycled by you (page): off 15 s, on (197 W before)")
        self.assertIsNone(self.pc.state()["busy"])

    def test_cycle_with_its_own_off_seconds(self):
        self.pc.cycle("cli", off_seconds=30)
        self.pc.join(5)
        self.assertEqual(self.slept, [30])
        self.assertIn("off 30 s", self.power_lines()[-1])

    def test_second_cycle_while_one_runs_is_refused(self):
        gate = threading.Event()
        self.pc = PowerControl(self.plug, self.power, self.wd, self.events, sleep=lambda s: gate.wait(5), clock=self.clock)
        self.pc.cycle("page")
        with self.assertRaises(PowerRefused):
            self.pc.cycle("page")
        gate.set()
        self.pc.join(5)

    def test_wrong_device_refuses_and_moves_nothing(self):
        self.plug.device_id = "other"
        for action in (self.pc.off, self.pc.on, self.pc.cycle):
            with self.assertRaises(PowerRefused):
                action("page")
        self.assertEqual(self.plug.calls, [])
        self.assertIsNone(self.wd.hold)

    def test_plug_not_answering_is_refused(self):
        self.plug.fail_identify = True
        with self.assertRaises(PowerRefused):
            self.pc.off("page")
        self.assertIsNone(self.wd.hold)

    def test_off_failure_is_logged_and_raised_without_a_hold(self):
        self.plug.fail_off = True
        with self.assertRaises(PowerRefused):
            self.pc.off("page")
        self.assertIn("power: switch off failed", self.power_lines()[-1])
        self.assertIsNone(self.wd.hold)

    def test_no_meter_says_so(self):
        self.plug.meter = False
        self.pc.off("page")
        self.assertEqual(self.power_lines()[-1], "power: switched off by you (page; no meter)")

    def test_without_a_watchdog_there_is_no_hold(self):
        pc = PowerControl(self.plug, self.power, None, self.events, sleep=self.slept.append, clock=self.clock)
        st = pc.off("page")
        self.assertFalse(self.plug.relay)
        self.assertIsNone(st["hold"])

    def test_state_reads_the_plug(self):
        st = self.pc.state()
        self.assertEqual(st, {"relay": True, "watts": 197.0, "busy": None, "off_by_you": False, "hold": None})
        self.plug.fail_identify = True
        self.assertIsNone(self.pc.state()["relay"])


if __name__ == "__main__":
    unittest.main()
