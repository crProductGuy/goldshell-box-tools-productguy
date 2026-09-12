"""The optional power block in config.json: absent means no plug; present means defaults merged and validated."""
import json
import tempfile
import unittest
from pathlib import Path

from gbox import config


class PowerConfigTest(unittest.TestCase):
    def test_no_power_block_means_none(self):
        self.assertIsNone(config.Config().power)
        self.assertIsNone(config.Config.from_dict({"host": "m"}).power)
        self.assertNotIn("power", config.Config().to_dict())

    def test_power_block_merges_defaults(self):
        cfg = config.Config.from_dict({"host": "m", "power": {"host": "p", "device_id": "abc"}})
        self.assertEqual(cfg.power["driver"], "kasa")
        self.assertEqual(cfg.power["host"], "p")
        self.assertEqual(cfg.power["device_id"], "abc")
        self.assertIs(cfg.power["cycle"], False)
        self.assertEqual(cfg.power["after_minutes"], 5)     # 2026-09-12: 15 cost 17 min of hashing per freeze
        self.assertEqual(config.DEFAULT_WATCHDOG["min_gap_minutes"], 5)
        self.assertEqual(cfg.power["off_seconds"], 15)
        self.assertEqual(cfg.power["settle_minutes"], 20)
        self.assertEqual(cfg.power["max_cycles_per_day"], 3)
        self.assertEqual(cfg.power["idle_watts"], 100)

    def test_round_trip_through_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(host="m", power={"host": "p", "device_id": "abc", "cycle": True})
            config.save(cfg, d)
            raw = json.loads((Path(d) / config.CONFIG_NAME).read_text())
            self.assertEqual(raw["power"]["host"], "p")
            self.assertIs(raw["power"]["cycle"], True)
            back = config.load(d)
            self.assertEqual(back.power["device_id"], "abc")
            self.assertIs(back.power["cycle"], True)

    def test_validate_rejects_unknown_driver(self):
        cfg = config.Config(host="m", power={"driver": "toaster", "host": "p"})
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_validate_rejects_off_seconds_out_of_range(self):
        for bad in (2, 121):
            cfg = config.Config(host="m", power={"host": "p", "off_seconds": bad})
            with self.assertRaises(ValueError):
                cfg.validate()

    def test_validate_rejects_after_minutes_below_unreachable_minutes(self):
        cfg = config.Config(host="m", watchdog={"unreachable_minutes": 5}, power={"host": "p", "after_minutes": 4})
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_validate_rejects_cap_out_of_range(self):
        for bad in (-1, 11):
            cfg = config.Config(host="m", power={"host": "p", "max_cycles_per_day": bad})
            with self.assertRaises(ValueError):
                cfg.validate()

    def test_validate_requires_a_plug_host(self):
        with self.assertRaises(ValueError):
            config.Config(host="m", power={"driver": "kasa"}).validate()

    def test_valid_block_passes(self):
        cfg = config.Config(host="m", power={"host": "p", "device_id": "abc"})
        self.assertIs(cfg.validate(), cfg)


class CapsFitTogetherTest(unittest.TestCase):
    """A power cycle needs two failed soft restarts, and each attempt uses a restart slot, so the restart cap
    must leave room for the cycle cap or the ladder silently stops one rung short."""

    def test_restart_cap_below_twice_the_cycle_cap_is_rejected(self):
        cfg = config.Config(host="m", watchdog={"max_restarts_per_day": 6}, power={"host": "p", "max_cycles_per_day": 8})
        with self.assertRaises(ValueError) as cm:
            cfg.validate()
        self.assertIn("max_restarts_per_day", str(cm.exception))

    def test_defaults_leave_room_for_the_default_cycle_cap(self):
        cfg = config.Config(host="m", power={"host": "p"}).validate()
        self.assertGreaterEqual(cfg.watchdog["max_restarts_per_day"], 2 * cfg.power["max_cycles_per_day"] + 2)

    def test_eight_cycles_with_twenty_restarts_passes(self):
        config.Config(host="m", watchdog={"max_restarts_per_day": 20}, power={"host": "p", "max_cycles_per_day": 8}).validate()


if __name__ == "__main__":
    unittest.main()
