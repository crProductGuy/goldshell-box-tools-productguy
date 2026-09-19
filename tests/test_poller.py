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
        self.assertEqual(lines[1], "2026-09-05 21:05:52,ok,1,2,3,4,0.4,5,0,600.0,3000,3000,70,70,63,0,8:1/1,,,,,,,,,")
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
        self.assertTrue(lines[1].endswith(",65,0,,,,,"))       # watts, chips and the three 0.7.0 log columns padded

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


class ChipsColumnPollerTest(unittest.TestCase):
    """The poller writes every chip's counts as the last column; a 22-column log from 0.5.x is migrated."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_chips_is_the_last_column_with_every_chip(self):
        self.assertEqual(poller.COLUMNS[22], "chips")         # last until 0.7.0 added the cgminer-log columns after it
        self.assertEqual(poller.COLUMNS[21], "watts")
        poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30).poll_once()
        row = self.rows()[0]
        parts = row["chips"].split(";")
        self.assertEqual(len(parts), 16)
        self.assertTrue(parts[0].startswith("0.1:"))          # the firmware numbers chips from 1
        self.assertIn("0.8", api.parse_chips(row["chips"]))

    def test_22_column_header_from_0_5_x_is_migrated(self):
        old = poller.COLUMNS[:22]
        self.assertEqual(old[-1], "watts")
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n")
            f.write("2026-09-13 15:52:32,ok,5,13015.584,13015.584,0,0.0,1,0,550.0,2880,2880,34.0,34.0,24.63,0,,10,0,65,0,174.476037\n")
        self.assertTrue(poller.migrate_columns(self.csv))
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.COLUMNS))
        self.assertTrue(lines[1].endswith(",174.476037,,,,"))
        self.assertEqual(self.rows()[0]["chips"], "")
        self.assertTrue((self.csv.parent / "log.csv.bak").exists())


class HottestChipPollerTest(unittest.TestCase):
    """0.7.0: every syslog_interval seconds the cycle reads the cgminer log once more and writes the hottest chip's\npeak, its sustained level (median) and the chip average as the last three columns; other rows leave them blank."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"
        self.now = 1_000_000.0

    def clock(self):
        return self.now

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def syslog_reads(self):
        return sum(1 for m, path in self.fm.requests if path == "/dbg/minersyslog")

    def test_columns_end_with_the_three_chip_temperature_fields(self):
        self.assertEqual(poller.COLUMNS[-4:], ["chips", "hot_peak", "hot_level", "chip_avg"])

    def test_first_cycle_reads_the_log_and_writes_peak_level_and_average(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        row = p.poll_once()
        self.assertEqual(row["http"], "ok")
        self.assertEqual(self.syslog_reads(), 1)
        r = self.rows()[0]
        self.assertEqual(r["hot_peak"], "93.0")           # the fixture's one spike
        self.assertEqual(r["hot_level"], "80.0")          # median of 65, 66, 79, 93, 81, 82
        self.assertEqual(r["chip_avg"], "69.0")           # median of 54, 55, 69, 69, 70, 70
        self.assertEqual(list(r.keys()), poller.COLUMNS)

    def test_between_reads_the_fields_are_blank_and_the_log_is_not_requested(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        p.poll_once()
        self.now += 30
        p.poll_once()
        self.assertEqual(self.syslog_reads(), 1)
        self.assertEqual((self.rows()[1]["hot_peak"], self.rows()[1]["hot_level"], self.rows()[1]["chip_avg"]), ("", "", ""))

    def test_the_next_read_takes_only_lines_newer_than_the_cursor(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        p.poll_once()
        self.now += 300
        p.poll_once()                                     # nothing new in the fake log: blank, not a repeat
        self.assertEqual(self.syslog_reads(), 2)
        self.assertEqual(self.rows()[1]["hot_peak"], "")
        self.fm.syslog += " [2026-09-15 07:41:31] C0: Chip Avgtemp 71.000000'C, MaxTemp 84.000000'C\n"
        self.now += 300
        p.poll_once()
        r = self.rows()[2]
        self.assertEqual((r["hot_peak"], r["hot_level"], r["chip_avg"]), ("84.0", "84.0", "71.0"))

    def test_first_read_after_start_keeps_the_last_five_minutes_only(self):
        self.fm.syslog = (" [2026-09-15 07:00:00] C0: Chip Avgtemp 50.000000'C, MaxTemp 60.000000'C\n"
                          " [2026-09-15 07:30:00] C0: Chip Avgtemp 70.000000'C, MaxTemp 80.000000'C\n"
                          " [2026-09-15 07:34:00] C0: Chip Avgtemp 70.000000'C, MaxTemp 82.000000'C\n")
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        p.poll_once()
        r = self.rows()[0]
        self.assertEqual((r["hot_peak"], r["hot_level"], r["chip_avg"]), ("82.0", "81.0", "70.0"))

    def test_readings_from_before_the_newest_boot_are_dropped(self):
        # the log survives a power cycle: the first read after a boot must not report the run before it
        self.fm.syslog += (" [2026-09-15 07:40:00] C0: SCBOX Init sucessed. 16 chips, 256 Total goodcores. Wait 5s!!!\n"
                           " [2026-09-15 07:40:05] C0: Chip Avgtemp 30.000000'C, MaxTemp 38.000000'C\n"
                           " [2026-09-15 07:40:10] C0: Chip Avgtemp 31.000000'C, MaxTemp 40.000000'C\n")
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        p.poll_once()
        r = self.rows()[0]
        self.assertEqual((r["hot_peak"], r["hot_level"], r["chip_avg"]), ("40.0", "39.0", "30.5"))

    def test_a_boot_with_no_reading_after_it_yet_is_a_blank_and_moves_the_cursor(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        p.poll_once()                                     # the fixture: readings up to 07:36:31
        self.fm.syslog += " [2026-09-15 07:40:00] C0: SCBOX Init sucessed. 16 chips, 256 Total goodcores. Wait 5s!!!\n"
        self.now += 300
        p.poll_once()
        self.assertEqual(self.rows()[1]["hot_level"], "")
        self.fm.syslog += " [2026-09-15 07:40:05] C0: Chip Avgtemp 30.000000'C, MaxTemp 38.000000'C\n"
        self.now += 300
        p.poll_once()
        self.assertEqual((self.rows()[2]["hot_peak"], self.rows()[2]["hot_level"]), ("38.0", "38.0"))

    def test_a_failed_log_read_is_a_blank_not_an_error_row(self):
        class NoLog(api.Miner):
            def syslog(self):
                raise api.MinerError("GET dbg/minersyslog: HTTP 500")
        p = poller.Poller(NoLog(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=300)
        row = p.poll_once()
        self.assertEqual(row["http"], "ok")
        self.assertEqual(self.rows()[0]["hot_peak"], "")
        self.assertEqual(p.errors, 0)
        self.assertEqual(p.syslog_errors, 1)

    def test_interval_zero_never_reads_the_log(self):
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, clock=self.clock, syslog_interval=0)
        p.poll_once()
        self.assertEqual(self.syslog_reads(), 0)

    def test_23_column_header_from_0_6_x_is_migrated(self):
        old = poller.COLUMNS[:23]
        self.assertEqual(old[-1], "chips")
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(old) + "\n")
            f.write("2026-09-14 15:52:32,ok,5,13015.584,13015.584,0,0.0,1,0,550.0,2880,2880,34.0,34.0,24.63,0,,10,0,65,0,174.476037,0.1:5/0\n")
        self.assertTrue(poller.migrate_columns(self.csv))
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.COLUMNS))
        self.assertTrue(lines[1].endswith(",0.1:5/0,,,"))
        self.assertEqual(self.rows()[0]["hot_level"], "")
        self.assertTrue((self.csv.parent / "log.csv.bak").exists())


class PollerHashingSignalTest(unittest.TestCase):
    """The poller tells the watchdog whether the sample was hashing, so a hold releases on hashing, not on answering."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    class Spy:
        def __init__(self):
            self.seen = []

        def observe(self, ok, accepted, t=None, hashing=None):
            self.seen.append((ok, hashing))

        def check(self):
            return None

    def test_hashing_is_true_for_a_live_sample_and_false_for_an_error_row(self):
        spy = self.Spy()
        poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, watchdog=spy).poll_once()
        poller.Poller(api.Miner("127.0.0.1:1", password="password", timeout=1), self.csv, 30, watchdog=spy).poll_once()
        self.assertEqual(spy.seen, [(True, True), (False, False)])

    def test_hashing_is_false_when_the_board_reports_no_hashrate(self):
        import re
        self.fm.minerinfo = re.sub(r"\[MHS (20s|av)\] => [^\n]*", r"[MHS \1] => 0.0", self.fm.minerinfo)
        spy = self.Spy()
        poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, watchdog=spy).poll_once()
        self.assertEqual(spy.seen, [(True, False)])


class PollerScheduleTest(unittest.TestCase):
    """The scheduler ticks once per sample, after the sample, and never fails one."""

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def test_tick_once_per_sample_even_when_it_raises(self):
        class Ticker:
            ticks = 0

            def tick(self):
                self.ticks += 1
                raise RuntimeError("boom")
        t = Ticker()
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, scheduler=t)
        self.assertEqual(p.poll_once()["http"], "ok")
        p.poll_once()
        self.assertEqual(t.ticks, 2)


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
        self.assertEqual(poller.COLUMNS[21], "watts")          # chips followed it in 0.6.0, the log columns in 0.7.0
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

    def test_syslog_interval_and_temps_default_round_trip_and_validate(self):
        cfg = config.load(self.tmp.name)
        self.assertEqual(cfg.syslog_interval, 300)
        self.assertEqual(cfg.temps, {"hot_serious": 85, "hot_critical": 90})
        config.save(config.Config(host="h", syslog_interval=0, temps={"hot_critical": 95}), self.tmp.name)
        back = config.load(self.tmp.name)
        self.assertEqual((back.syslog_interval, back.temps["hot_serious"], back.temps["hot_critical"]), (0, 85, 95))
        back.validate()
        with self.assertRaises(ValueError):
            config.Config(syslog_interval=30).validate()               # under the 60 s floor and not off
        with self.assertRaises(ValueError):
            config.Config(temps={"hot_serious": 90, "hot_critical": 85}).validate()
        with self.assertRaises(ValueError):
            config.Config(temps={"hot_serious": 30}).validate()

    def test_power_boot_check_defaults_and_validation(self):
        cfg = config.Config(host="h", power={"host": "p"})
        self.assertEqual((cfg.power["boot_watts"], cfg.power["boot_check_minutes"]), (20, 2))
        cfg.validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "boot_watts": 100}).validate()      # not below idle_watts
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "boot_check_minutes": 11}).validate()
        config.Config(host="h", power={"host": "p", "boot_check_minutes": 0}).validate()

    def test_settle_minutes_is_bounded_and_must_cover_the_off_period(self):
        """It was the one power value with no bound, and it is the one that suppresses the stall check: at 20 it
        hid a dead hashboard for 22 minutes (2026-09-17), so a typo of 600 would hide one for ten hours. The
        floor is derived rather than flat because the gap is timed from the relay OPENING: off_seconds is spent
        before the miner has begun to boot, so a flat "at least 2 minutes" would still leave nothing at all at
        off_seconds 120, the maximum the validator allows."""
        self.assertEqual(config.Config(host="h", power={"host": "p"}).power["settle_minutes"], 6)
        config.Config(host="h", power={"host": "p", "settle_minutes": 60}).validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "settle_minutes": 61}).validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "settle_minutes": -1}).validate()
        # off_seconds 15 (the default) needs 75 s, so 2 minutes clears it and 1 does not
        config.Config(host="h", power={"host": "p", "settle_minutes": 2}).validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "settle_minutes": 1}).validate()
        # off_seconds 120 (the owner's live setting) needs 180 s: 2 minutes is entirely eaten by the off period
        config.Config(host="h", power={"host": "p", "off_seconds": 120, "settle_minutes": 3}).validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "off_seconds": 120, "settle_minutes": 2}).validate()
        with self.assertRaises(ValueError):
            config.Config(host="h", power={"host": "p", "settle_minutes": 0}).validate()

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
