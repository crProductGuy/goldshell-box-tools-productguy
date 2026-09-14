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
        p = api.parse_plan("600 MHz 0.41 V 90 RPM 90 RPM")
        self.assertEqual((p["mhz"], p["volts"], p["fan_a"], p["fan_b"], p["dialect"]), (600, 0.41, 90, 90, "box"))
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


class ChipsColumnTest(unittest.TestCase):
    """The all-chips column: every chip's counts, board-aware, round-tripped."""

    def boards(self):
        return api.parse_icinfo(fixture("dbg_icinfo.json"))

    def test_format_is_board_dot_chip_in_order(self):
        text = api.format_chips(self.boards())
        parts = text.split(";")
        self.assertEqual(len(parts), 16)
        self.assertTrue(parts[0].startswith("0.1:"))          # the firmware numbers chips from 1
        self.assertTrue(parts[7].startswith("0.8:"))
        self.assertRegex(parts[7], r"^0\.8:\d+/\d+$")
        self.assertEqual(api.format_chips([]), "")

    def test_two_boards_carry_their_index(self):
        boards = [[{"chip": 0, "good": 5, "bad": 1}], [{"chip": 0, "good": 7, "bad": 0}, {"chip": 1, "good": 8, "bad": 2}]]
        self.assertEqual(api.format_chips(boards), "0.0:5/1;1.0:7/0;1.1:8/2")

    def test_parse_round_trips_and_ignores_junk(self):
        self.assertEqual(api.parse_chips("0.0:5/1;1.0:7/0"), {"0.0": (5, 1), "1.0": (7, 0)})
        self.assertEqual(api.parse_chips(""), {})
        self.assertEqual(api.parse_chips(None), {})
        self.assertEqual(api.parse_chips("garbage;0.3:1/x;0.4:2/3"), {"0.4": (2, 3)})
        text = api.format_chips(self.boards())
        self.assertEqual(len(api.parse_chips(text)), 16)


class PlanDialectTest(unittest.TestCase):
    """The power plan string has dialects (docs/firmware-api.md, "Power plan dialects"). Parsing is driven by the
    string, not by the model table, and formatting writes back exactly what was read except the clock."""

    BOX = "575 MHz 0.41 V 90 RPM 90 RPM"                     # SC-BOX, read from the unit
    MV_PV = "625 MHz 9100 V 40 RPM 40 RPM PV 9400"            # SC Lite fw 2.2.0, from the other developer's notes
    FLOAT_PV = "750 MHz 0.41 V 50 RPM 50 RPM PV 9400"         # the documented "float-V / optional-PV" form; no verbatim example on record

    def test_parse_returns_the_parts_and_names_the_dialect(self):
        self.assertEqual(api.parse_plan(self.BOX), {"mhz": 575, "volts": 0.41, "fan_a": 90, "fan_b": 90, "pv": None,
                                                    "volts_text": "0.41", "dialect": "box"})
        self.assertEqual(api.parse_plan(self.MV_PV), {"mhz": 625, "volts": 9100.0, "fan_a": 40, "fan_b": 40, "pv": 9400,
                                                      "volts_text": "9100", "dialect": "mv_pv"})
        self.assertEqual(api.parse_plan(self.FLOAT_PV)["dialect"], "float_pv")
        self.assertEqual(api.parse_plan(self.FLOAT_PV)["pv"], 9400)
        self.assertEqual(api.parse_plan("0 MHz 0 V 70 RPM 70 RPM")["dialect"], "box")    # the BOX's level-3 preset

    def test_format_round_trips_every_dialect_verbatim(self):
        for s in (self.BOX, self.MV_PV, self.FLOAT_PV, "0 MHz 0 V 70 RPM 70 RPM", "725 MHz 0.40 V 70 RPM 70 RPM"):
            self.assertEqual(api.format_plan(**api.parse_plan(s)), s)

    def test_with_mhz_changes_only_the_clock(self):
        self.assertEqual(api.with_mhz(self.BOX, 550), "550 MHz 0.41 V 90 RPM 90 RPM")
        self.assertEqual(api.with_mhz(self.MV_PV, 600), "600 MHz 9100 V 40 RPM 40 RPM PV 9400")
        self.assertEqual(api.with_mhz(self.FLOAT_PV, 725), "725 MHz 0.41 V 50 RPM 50 RPM PV 9400")
        self.assertEqual(api.with_mhz("  575 MHz  0.41 V 90 RPM 90 RPM ", 550), "550 MHz 0.41 V 90 RPM 90 RPM")
        self.assertEqual(api.with_mhz(self.BOX, 550, volts=0.4), "550 MHz 0.4 V 90 RPM 90 RPM")
        with self.assertRaises(ValueError):
            api.with_mhz("hashrate mode", 550)

    def test_rejects_what_no_firmware_writes(self):
        for bad in ("625 MHz 9100 V 40 RPM", "625 MHz 9100 V 40 RPM 40 RPM PV", "625 MHz 9100 V 40 RPM 40 RPM PV x",
                    "625 MHz 9100 V 40 RPM 40 RPM PV 9400 extra", "", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                api.parse_plan(bad)

    def test_positional_format_is_the_box_form(self):
        self.assertEqual(api.format_plan(625, 0.41, 90, 90), "625 MHz 0.41 V 90 RPM 90 RPM")
        self.assertEqual(api.format_plan(725, 0.4, 70, 70), "725 MHz 0.4 V 70 RPM 70 RPM")
        self.assertEqual(api.format_plan(625, 9100, 40, 40, pv=9400), "625 MHz 9100 V 40 RPM 40 RPM PV 9400")


class SCLiteFixtureTest(unittest.TestCase):
    """The synthetic SC Lite fixtures (tests/fixtures/sclite, from the other developer's notes) pick the SC Lite
    profile and parse, so the seam is exercised before a unit is on the bench."""

    def test_status_picks_the_profile(self):
        from gbox import models
        st = json.loads(fixture("sclite/mcb_status.json"))
        p = models.profile_for(st["model"])
        self.assertTrue(p["known"])
        self.assertEqual(p["name"], "SC Lite")
        self.assertEqual(p["plan_dialect"], "mv_pv")

    def test_setting_parses_in_its_dialect(self):
        st = json.loads(fixture("sclite/mcb_setting.json"))
        self.assertEqual(api.parse_plan(st["manualPowerplan"])["dialect"], "mv_pv")
        self.assertEqual(api.max_preset_mhz(st), 625)
        self.assertNotIn("temp_targets", st)          # no fan-target range on this firmware, per the notes
