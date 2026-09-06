"""Parsers against the sanitized fixtures in tests/fixtures."""
import json
import os
import unittest

from gbox import api

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class MinerInfoTest(unittest.TestCase):
    def setUp(self):
        self.info = api.parse_minerinfo(fixture("dbg_minerinfo.txt"))

    def test_numeric_fields(self):
        self.assertEqual(self.info["elapsed"], 37623)
        self.assertAlmostEqual(self.info["mhs_av"], 713260.285)
        self.assertAlmostEqual(self.info["mhs_20s"], 819870.331)
        self.assertEqual(self.info["accepted"], 17582)
        self.assertEqual(self.info["rejected"], 4711)
        self.assertEqual(self.info["hw_errors"], 2894)
        self.assertAlmostEqual(self.info["hw_pct"], 2.6789)
        self.assertAlmostEqual(self.info["clock"], 600.0)
        self.assertEqual(self.info["fan0"], 3120)
        self.assertEqual(self.info["fan1"], 3060)
        self.assertAlmostEqual(self.info["chip_temp"], 73.0)
        self.assertAlmostEqual(self.info["board_temp"], 65.13)
        self.assertEqual(self.info["rebootcnt"], 355)
        self.assertEqual(self.info["overheat"], 0)

    def test_missing_key_is_none(self):
        self.assertIsNone(api.parse_minerinfo("[PGA0] =>\n(\n   [clock] => 600\n)\n")["fan0"])

    def test_raw_kv(self):
        self.assertEqual(api.kv(fixture("dbg_minerinfo.txt"), "Name"), "INCS")
        self.assertIsNone(api.kv("", "Name"))


class IcInfoTest(unittest.TestCase):
    def test_boards_and_chips(self):
        boards = api.parse_icinfo(fixture("dbg_icinfo.json"))
        self.assertEqual(len(boards), 1)
        self.assertEqual(len(boards[0]), 16)
        first = boards[0][0]
        self.assertEqual(first, {"chip": 1, "good": 7001, "bad": 5})

    def test_weak_chip_flags_are_relative(self):
        chips = [{"chip": i, "good": 1000, "bad": 3} for i in range(1, 5)]
        chips[2] = {"chip": 3, "good": 600, "bad": 3}      # below 70 percent of the best
        chips[3] = {"chip": 4, "good": 1000, "bad": 41}    # too many bad nonces
        flags = api.weak_chips(chips)
        self.assertEqual([c["chip"] for c in flags], [3, 4])

    def test_failing_chip(self):
        chips = [{"chip": 1, "good": 1000, "bad": 3}, {"chip": 2, "good": 100, "bad": 900}]
        self.assertEqual(api.chip_health(chips[1], 1000), "failing")
        self.assertEqual(api.chip_health(chips[0], 1000), "ok")

    def test_fixture_flags_marginal_chips(self):
        boards = api.parse_icinfo(fixture("dbg_icinfo.json"))
        weak = [c["chip"] for c in api.weak_chips(boards[0])]
        self.assertIn(8, weak)


class SettingsTest(unittest.TestCase):
    def test_plan_round_trip(self):
        self.assertEqual(api.parse_plan("600 MHz 0.41 V 90 RPM 90 RPM"), (600, 0.41, 90, 90))
        self.assertEqual(api.format_plan(625, 0.41, 90, 90), "625 MHz 0.41 V 90 RPM 90 RPM")
        self.assertEqual(api.format_plan(725, 0.4, 70, 70), "725 MHz 0.4 V 70 RPM 70 RPM")

    def test_plan_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            api.parse_plan("hashrate mode")

    def test_fixture_setting_loads(self):
        st = json.loads(fixture("mcb_setting.json"))
        self.assertTrue(st["manual"])
        self.assertEqual(st["temp_targets"], [65.0, 75.0])
        self.assertEqual(api.max_preset_mhz(st), 725)

    def test_status_fixture(self):
        st = json.loads(fixture("mcb_status.json"))
        self.assertEqual(st["model"], "Goldshell-SCBox")


class HashrateUnitsTest(unittest.TestCase):
    def test_autoscale(self):
        self.assertEqual(api.hashrate_unit(500.0), ("MH/s", 1))
        self.assertEqual(api.hashrate_unit(713260.285), ("GH/s", 1000))
        self.assertEqual(api.hashrate_unit(48000000.0), ("TH/s", 1000000))
        self.assertEqual(api.hashrate_unit(0), ("MH/s", 1))
        self.assertEqual(api.hashrate_unit(None), ("MH/s", 1))


if __name__ == "__main__":
    unittest.main()
