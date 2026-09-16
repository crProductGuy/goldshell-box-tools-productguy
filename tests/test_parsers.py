"""Parsers against the sanitized fixtures in tests/fixtures."""
import json
import os
import socket
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


class BoardsTest(unittest.TestCase):
    """`parse_minerinfo_boards` + `board_totals` (gate2-pga-0.8.0 section A, task 3)."""

    # The SC-BOX's today's dict, written out literally from the current `parse_minerinfo` output before
    # the rewire (regression guard: if this changes after the rewire, that is a STOP, not a test to edit).
    TODAY_SCBOX = {
        "elapsed": 37623, "mhs_av": 713260.285, "mhs_20s": 819870.331,
        "accepted": 17582, "rejected": 4711, "hw_errors": 2894, "hw_pct": 2.6789,
        "clock": 600.0, "fan0": 3120, "fan1": 3060, "chip_temp": 73.0,
        "chip_temp1": 73.0, "board_temp": 65.13, "rebootcnt": 355, "overheat": 0,
    }

    def test_a_scbox_minerinfo_totals_are_todays_dict_literally(self):
        info = api.parse_minerinfo(fixture("dbg_minerinfo.txt"))
        subset = {k: info[k] for k in self.TODAY_SCBOX}
        self.assertEqual(subset, self.TODAY_SCBOX)

    def test_b_sc5proii_four_boards_and_totals(self):
        text = fixture("sc5proii/dbg_minerinfo.txt")
        boards = api.parse_minerinfo_boards(text)
        self.assertEqual(len(boards), 4)
        self.assertEqual(boards[0]["board"], 0)
        self.assertEqual(boards[3]["board"], 3)
        self.assertAlmostEqual(boards[0]["chip_temp"], 89.0)
        self.assertAlmostEqual(boards[3]["chip_temp"], 85.0)
        self.assertEqual(boards[0]["fans"], [3360, 3360, 3480, 3480])

        totals = api.board_totals(boards)
        self.assertEqual(totals["nboards"], 4)
        self.assertAlmostEqual(totals["mhs_av"], sum(b["mhs_av"] for b in boards))
        self.assertEqual(totals["hot_board"], 0)
        self.assertAlmostEqual(totals["chip_temp"], 89.0)
        self.assertIsNotNone(totals["watts_dc"])
        self.assertAlmostEqual(totals["watts_dc"], 3042, delta=1)

    def test_c_garbage_is_one_board_of_nones(self):
        boards = api.parse_minerinfo_boards("garbage not real data")
        self.assertEqual(len(boards), 1)
        b = boards[0]
        self.assertEqual(b["board"], 0)
        self.assertEqual(b["fans"], [])
        self.assertIsNone(b["voltage_mv"])
        self.assertIsNone(b["current_ma"])
        for key in self.TODAY_SCBOX:
            self.assertIsNone(b[key], key)

        totals = api.board_totals(boards)
        self.assertEqual(totals["nboards"], 1)
        self.assertEqual(totals["hot_board"], 0)
        self.assertIsNone(totals["watts_dc"])
        for key in self.TODAY_SCBOX:
            self.assertIsNone(totals[key], key)


class Devs4028Test(unittest.TestCase):
    """`parse_devs4028` + `Miner.devs4028` (gate2-pga-0.8.0 section A, task 4)."""

    def test_sc5proii_devs4028_same_shape_as_minerinfo_board0(self):
        boards = api.parse_devs4028(fixture("sc5proii/api4028_devs.json"))
        self.assertEqual(len(boards), 4)
        self.assertEqual(boards[0]["board"], 0)
        self.assertAlmostEqual(boards[0]["chip_temp"], 90.0)
        self.assertEqual(boards[0]["fans"], [3240, 3240, 3360, 3360])

    def test_trailing_nul_is_tolerated(self):
        raw = fixture("sc5proii/api4028_devs.json").rstrip("\n") + "\x00"
        boards = api.parse_devs4028(raw)
        self.assertEqual(len(boards), 4)

    def test_fake_miner_devs4028_served_through_miner(self):
        from tests.fake_miner import FakeMiner
        with FakeMiner(fixtures="sc5proii", port4028=True) as fm:
            m = api.Miner(fm.address, password="password")
            boards = m.devs4028(port=fm.devs4028_port)
            self.assertEqual(len(boards), 4)
            self.assertAlmostEqual(boards[0]["chip_temp"], 90.0)

    def test_devs4028_refused_is_minererror(self):
        from tests.fake_miner import FakeMiner
        with FakeMiner(fixtures="sc5proii", port4028=False) as fm:
            self.assertIsNone(fm.devs4028_port)
            m = api.Miner(fm.address, password="password")
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            closed_port = s.getsockname()[1]
            s.close()                              # a definitely-closed port: guaranteed connection refused
            with self.assertRaises(api.MinerError):
                m.devs4028(port=closed_port)

    def test_scbox_4028_totals_match_minerinfo_totals_keys_not_values(self):
        devs_totals = api.board_totals(api.parse_devs4028(fixture("api4028_devs.json")))
        info_totals = api.parse_minerinfo(fixture("dbg_minerinfo.txt"))
        for key in BoardsTest.TODAY_SCBOX:
            self.assertEqual(devs_totals[key] is None, info_totals[key] is None, key)
        self.assertEqual(devs_totals["nboards"], 1)
        self.assertIsNone(devs_totals["watts_dc"])


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


class SC5ProIIFixtureTest(unittest.TestCase):
    """The captured SC5 Pro II fixtures (tests/fixtures/sc5proii, a friend's unit, gate2-pga-0.8.0 section E): every
    file parses or loads, and none of them carries a pool string or a credential."""

    DIR = os.path.join(FIX, "sc5proii")
    NAMES = [n for n in sorted(os.listdir(DIR)) if n != "README.md"]

    def test_every_file_parses_or_loads(self):
        for name in self.NAMES:
            text = fixture(os.path.join("sc5proii", name))
            if name.endswith(".json"):
                json.loads(text)                        # every *.json fixture is valid JSON on its own
            else:
                self.assertIsNotNone(api.parse_minerinfo(text), name)   # dbg_minerinfo.txt: today's kv parser still works

    def test_no_pool_string_or_credential(self):
        for name in self.NAMES:
            text = fixture(os.path.join("sc5proii", name))
            self.assertNotIn("stratum", text, name)
            self.assertNotIn("@", text, name)

    def test_fake_miner_serves_the_sc5proii_fixtures(self):
        from tests.fake_miner import FakeMiner
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            self.assertEqual(fm.status["model"], "Goldshell-SC5ProⅡ")
            self.assertIsNone(fm.icinfo)             # no dbg_icinfo.json capture for this unit
            self.assertIsNotNone(fm.devs4028_port)
            import socket
            with socket.create_connection((fm.host, fm.devs4028_port), timeout=5) as s:
                s.sendall(b'{"command":"devs"}')
                s.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
            self.assertTrue(raw.endswith(b"\x00"), raw[-10:])
            parsed = json.loads(raw.rstrip(b"\x00").decode("utf-8"))
            self.assertEqual(len(parsed["DEVS"]), 4)

    def test_fake_miner_port4028_false_means_refused(self):
        from tests.fake_miner import FakeMiner
        with FakeMiner(fixtures="sc5proii") as fm:
            self.assertIsNone(fm.devs4028_port)
