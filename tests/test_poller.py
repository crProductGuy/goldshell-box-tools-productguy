"""Poller and config: one sample to CSV, error rows, config round trip."""
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

from gbox import api, config, poller
from gbox.events import EventLog
from gbox.watchdog import Watchdog
from tests.fake_miner import FakeMiner


class PollerTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_one_sample_writes_header_and_row(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30)
        row = p.poll_once()
        self.assertEqual(row["http"], "ok")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["clock"], "600.0")
        self.assertEqual(rows[0]["accepted"], "17582")
        self.assertIn("8:", rows[0]["weak_chips"])
        self.assertEqual(list(rows[0].keys()), poller.COLUMNS)
        self.assertEqual(p.samples, 1)
        self.assertEqual(p.latest["time"], rows[0]["time"])

    def test_error_row_has_no_url_and_keeps_column_count(self):
        p = poller.Poller(api.Miner("127.0.0.1:1", password="password", timeout=1), self.csv, 30)
        row = p.poll_once()
        self.assertTrue(row["http"].startswith("ERR:MinerError"))
        self.assertNotIn("http://", row["http"])
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[1].count(","), len(poller.COLUMNS) - 1)
        self.assertEqual(p.errors, 1)

    def test_waiting_for_token_is_not_fed_to_watchdog(self):
        events = EventLog()
        wd = Watchdog(lambda: None, events, 30)
        p = poller.Poller(api.Miner(self.fm.address), self.csv, 30, watchdog=wd)
        row = p.poll_once()
        self.assertIn("NoCredentials", row["http"])
        self.assertEqual(len(wd._rows), 0)

    def test_sample_has_nonce_totals_fan_target_and_overheat(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30)
        p.poll_once()
        r = self.rows()[0]
        self.assertEqual(r["nonces_good"], "108662")      # sum of perf over every chip in dbg_icinfo.json
        self.assertEqual(r["nonces_bad"], "2894")
        self.assertEqual(r["temp_target"], "65")
        self.assertEqual(r["overheat"], "0")
        self.assertEqual(poller.COLUMNS[17:21], ["nonces_good", "nonces_bad", "temp_target", "overheat"])

    def test_old_header_is_migrated_with_a_backup(self):
        old = poller.COLUMNS[:17]
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n")
            f.write("2026-09-05 21:05:52,ok,1,2,3,4,0.4,5,0,600.0,3000,3000,70,70,63,0,8:1/1\n")
            f.write("2026-09-05 21:06:22,ERR:x,,,,,,,,,,,,,,,\n")
        self.assertTrue(poller.migrate_columns(self.csv))
        self.assertFalse(poller.migrate_columns(self.csv))          # already current: a no-op
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.COLUMNS))
        self.assertEqual(lines[1], "2026-09-05 21:05:52,ok,1,2,3,4,0.4,5,0,600.0,3000,3000,70,70,63,0,8:1/1,,,,,")
        self.assertEqual(lines[2].count(","), len(poller.COLUMNS) - 1)
        self.assertTrue((self.csv.parent / "log.csv.bak").exists())
        rows = self.rows()
        self.assertEqual(rows[0]["nonces_good"], "")
        self.assertEqual(rows[0]["weak_chips"], "8:1/1")
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30)
        p.poll_once()
        self.assertEqual(self.rows()[2]["temp_target"], "65")

    def test_migrate_ignores_missing_or_foreign_header(self):
        self.assertFalse(poller.migrate_columns(self.csv))
        with open(self.csv, "w", encoding="utf-8") as f:
            f.write("a,b,c\n1,2,3\n")
        self.assertFalse(poller.migrate_columns(self.csv))
        with open(self.csv, encoding="utf-8") as f:
            self.assertEqual(f.read(), "a,b,c\n1,2,3\n")

    def test_watchdog_is_fed(self):
        events = EventLog()
        wd = Watchdog(lambda: None, events, 30)
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, watchdog=wd)
        p.poll_once()
        self.assertEqual(len(wd._rows), 1)
        self.assertEqual(wd._rows[0][1:], (True, 17582))

    def test_21_column_header_from_0_2_0_is_migrated(self):
        old = poller.COLUMNS[:21]
        self.assertEqual(old[-1], "overheat")
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n")
            f.write("2026-09-08 21:28:17,ok,1,2,3,4,0.4,5,0,575.0,1200,1200,70,70,63,0,8:1/1,556876,1711,65,0\n")
        self.assertTrue(poller.migrate_columns(self.csv))
        self.assertTrue(self.csv.with_name("log.csv.bak").exists())
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.COLUMNS))
        self.assertTrue(lines[1].endswith(",65,0,"))

    def test_migration_note_says_whether_the_backup_is_new(self):
        """The first migration writes log.csv.bak; a later one keeps the older backup and says so."""
        old = poller.COLUMNS[:21]
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n2026-09-08 21:28:17,ok,1,2,3,4,0.4,5,0,575.0,1200,1200,70,70,63,0,8:1/1,556876,1711,65,0\n")
        note = poller.migrate_columns(self.csv)
        self.assertEqual(note, "copy kept as log.csv.bak")
        backup = self.csv.with_name("log.csv.bak")
        first_backup = backup.read_text(encoding="utf-8")
        # a second, later migration: the log is short again (as after a downgrade), the old backup stays
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n2026-09-09 10:00:00,ok,1,2,3,4,0.4,5,0,575.0,1200,1200,70,70,63,0,8:1/1,1,2,65,0\n")
        note = poller.migrate_columns(self.csv)
        self.assertEqual(note, "the older log.csv.bak was left as is")
        self.assertEqual(backup.read_text(encoding="utf-8"), first_backup)
        self.assertFalse(poller.migrate_columns(self.csv))          # current header: no note, no change


class PollerPlugTest(unittest.TestCase):
    """The watts column: read from the plug after the miner sample, empty without a meter, never a failed sample."""

    def setUp(self):
        from gbox.plug import KasaLegacy
        from tests.fake_plug import FakePlug
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.fake = FakePlug(watts=188.0).start()
        self.addCleanup(self.fake.stop)
        self.plug = KasaLegacy(self.fake.address, timeout=1.0)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"
        self.lines = []
        self.events = EventLog()
        self.events.write = lambda m: self.lines.append(m)

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_watts_is_the_last_column_and_empty_without_a_plug(self):
        self.assertEqual(poller.COLUMNS[-1], "watts")
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30)
        p.poll_once()
        self.assertEqual(self.rows()[0]["watts"], "")
        self.assertIsNone(p.plug_watts)

    def test_watts_and_relay_read_from_the_plug(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, plug=self.plug, events=self.events)
        p.poll_once()
        self.assertEqual(self.rows()[0]["watts"], "188.0")
        self.assertEqual(p.plug_watts, 188.0)
        self.assertIs(p.plug_state, True)
        self.assertEqual(p.plug_info["model"], "HS110(US)")
        self.assertEqual(self.lines, [])

    def test_no_meter_leaves_the_column_empty_but_reads_the_relay(self):
        self.fake.meter = None
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, plug=self.plug, events=self.events)
        p.poll_once()
        self.assertEqual(self.rows()[0]["watts"], "")
        self.assertIs(p.plug_state, True)

    def test_watts_read_even_when_the_miner_sample_failed(self):
        p = poller.Poller(api.Miner("127.0.0.1:1", password="password", timeout=1), self.csv, 30, plug=self.plug)
        row = p.poll_once()
        self.assertTrue(row["http"].startswith("ERR:"))
        self.assertEqual(self.rows()[0]["watts"], "188.0")

    def test_plug_outage_keeps_the_sample_and_logs_one_line_per_transition(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, plug=self.plug, events=self.events)
        p.poll_once()
        self.fake.hang = True
        for _ in range(3):
            row = p.poll_once()
            self.assertEqual(row["http"], "ok")
        self.assertEqual(self.rows()[-1]["watts"], "")
        self.assertIsNone(p.plug_state)
        self.assertEqual(sum("plug unreachable" in l for l in self.lines), 1)
        self.fake.hang = False
        p.poll_once()
        p.poll_once()
        self.assertEqual(self.rows()[-1]["watts"], "188.0")
        self.assertEqual(sum("plug back" in l for l in self.lines), 1)
        self.assertEqual(len(self.lines), 2)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_defaults_when_missing(self):
        cfg = config.load(self.tmp.name)
        self.assertEqual(cfg.host, "")
        self.assertEqual(cfg.bind, "127.0.0.1")
        self.assertTrue(cfg.watchdog["enabled"])

    def test_round_trip_without_secret(self):
        cfg = config.Config(host="192.0.2.10", poll_interval=20, watchdog={"max_restarts_per_day": 2})
        path = config.save(cfg, self.tmp.name)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.assertNotIn("password_hex", d)
        back = config.load(self.tmp.name)
        self.assertEqual(back.host, "192.0.2.10")
        self.assertEqual(back.poll_interval, 20)
        self.assertEqual(back.watchdog["max_restarts_per_day"], 2)
        self.assertEqual(back.watchdog["stall_minutes"], 5)

    def test_remembered_secret_round_trips(self):
        config.save(config.Config(host="h", password_hex="00" * 16), self.tmp.name)
        self.assertEqual(config.load(self.tmp.name).password_hex, "00" * 16)
        if os.name == "posix":
            self.assertEqual(os.stat(Path(self.tmp.name) / "config.json").st_mode & 0o777, 0o600)

    def test_validate_rejects_fast_polling(self):
        with self.assertRaises(ValueError):
            config.Config(poll_interval=5).validate()
        config.Config(poll_interval=10).validate()

    def test_data_dir_env(self):
        old = os.environ.get("GBOX_DATA")
        os.environ["GBOX_DATA"] = self.tmp.name
        try:
            self.assertEqual(config.default_data_dir(), Path(self.tmp.name))
        finally:
            if old is None:
                del os.environ["GBOX_DATA"]
            else:
                os.environ["GBOX_DATA"] = old


if __name__ == "__main__":
    unittest.main()
