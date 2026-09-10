"""The Kasa legacy driver against the fake plug: framing, identity, meter shapes, relay, cycle, discovery."""
import unittest

from gbox import plug as plugmod
from gbox.plug import KasaLegacy, PlugError
from tests.fake_plug import FakePlug


class KasaLegacyTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakePlug().start()
        self.addCleanup(self.fake.stop)
        self.plug = KasaLegacy(self.fake.address, timeout=2.0)

    def test_identify_reads_model_alias_device_id_and_meter(self):
        info = self.plug.identify()
        self.assertEqual(info["model"], "HS110(US)")
        self.assertEqual(info["alias"], "fake plug")
        self.assertEqual(info["device_id"], self.fake.device_id)
        self.assertEqual(info["hw"], "1.0")
        self.assertTrue(info["fw"].startswith("1.2.6"))
        self.assertTrue(info["meter"])

    def test_identify_meter_false_when_emeter_unsupported(self):
        self.fake.meter = None
        self.assertFalse(self.plug.identify()["meter"])

    def test_watts_old_field_shape_is_watts(self):
        self.fake.meter, self.fake.watts = "old", 187.8
        self.assertAlmostEqual(self.plug.watts(), 187.8, places=3)

    def test_watts_new_field_shape_converts_milliwatts(self):
        self.fake.meter, self.fake.watts = "new", 33.6
        self.assertAlmostEqual(self.plug.watts(), 33.6, places=3)

    def test_watts_none_without_meter(self):
        self.fake.meter = None
        self.assertIsNone(self.plug.watts())

    def test_state_follows_relay(self):
        self.fake.relay = 1
        self.assertTrue(self.plug.state())
        self.fake.relay = 0
        self.assertFalse(self.plug.state())

    def test_off_and_on_send_one_relay_command_each(self):
        self.plug.off()
        self.assertEqual(self.fake.relay, 0)
        self.plug.on()
        self.assertEqual(self.fake.relay, 1)
        sent = [c for c in self.fake.commands if c[1] == "set_relay_state"]
        self.assertEqual([c[2] for c in sent], [{"state": 0}, {"state": 1}])

    def test_reply_split_across_writes_is_reassembled(self):
        self.fake.split_reply = True
        self.assertEqual(self.plug.identify()["model"], "HS110(US)")

    def test_hung_plug_raises_within_timeout(self):
        self.fake.hang = True
        quick = KasaLegacy(self.fake.address, timeout=0.3)
        with self.assertRaises(PlugError):
            quick.state()

    def test_unreachable_address_raises_plug_error(self):
        with self.assertRaises(PlugError):
            KasaLegacy("127.0.0.1:1", timeout=0.5).state()

    def test_cycle_turns_off_waits_then_on(self):
        waited = []
        self.plug.cycle(15, sleep=waited.append)
        self.assertEqual(waited, [15])
        self.assertEqual(self.fake.relay, 1)
        states = [c[2]["state"] for c in self.fake.commands if c[1] == "set_relay_state"]
        self.assertEqual(states, [0, 1])

    def test_cycle_still_attempts_on_when_off_failed(self):
        self.fake.fail_once = "set_relay_state"          # the off command gets no reply
        with self.assertRaises(PlugError):
            self.plug.cycle(15, sleep=lambda s: None)
        states = [c[2]["state"] for c in self.fake.commands if c[1] == "set_relay_state"]
        self.assertEqual(states, [0, 1])
        self.assertEqual(self.fake.relay, 1)

    def test_describe_is_one_line(self):
        self.assertEqual(self.plug.describe(), "HS110(US), on, 188 W")
        self.fake.meter, self.fake.relay = None, 0
        self.assertEqual(self.plug.describe(), "HS110(US), off, no meter")

    def test_multi_outlet_plug_is_refused(self):
        self.fake.sysinfo = lambda: {"model": "HS300(US)", "children": [], "err_code": 0, "deviceId": "x", "alias": "strip"}
        with self.assertRaises(PlugError):
            self.plug.state()


class DiscoverTest(unittest.TestCase):
    def test_finds_a_legacy_plug_once_with_its_meter(self):
        with FakePlug(udp_port=0, watts=188.0) as fake:
            found = plugmod.discover(timeout=0.5, port=fake.udp_port, klap_port=1, targets=("127.0.0.1", "127.0.0.1"))
        self.assertEqual(len(found), 1)
        d = found[0]
        self.assertEqual(d["host"], "127.0.0.1")
        self.assertEqual(d["model"], "HS110(US)")
        self.assertEqual(d["protocol"], "legacy")
        self.assertTrue(d["relay"])
        self.assertTrue(d["meter"])
        self.assertAlmostEqual(d["watts"], 188.0, places=3)

    def test_nothing_answering_is_an_empty_list(self):
        self.assertEqual(plugmod.discover(timeout=0.3, port=1, klap_port=1, targets=("127.0.0.1",)), [])


class MakeTest(unittest.TestCase):
    def test_kasa_driver_from_config(self):
        p = plugmod.make({"driver": "kasa", "host": "127.0.0.1:9998"})
        self.assertIsInstance(p, KasaLegacy)

    def test_unknown_driver_is_a_value_error(self):
        with self.assertRaises(ValueError):
            plugmod.make({"driver": "toaster", "host": "x"})


if __name__ == "__main__":
    unittest.main()
