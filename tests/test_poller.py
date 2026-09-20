"""Poller and config: one sample to CSV, error rows, config round trip."""
import csv
import datetime
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
        self.assertEqual(lines[1], "2026-09-05 21:05:52,ok,1,2,3,4,0.4,5,0,600.0,3000,3000,70,70,63,0,8:1/1,,,,,,,,,,")
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

    def test_poller_rejects_unknown_board_source(self):
        with self.assertRaises(ValueError):
            poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, board_source="devs")

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
        # watts, chips, the three 0.7.0 log columns and 0.8.0's watts_dc: six empty fields padded
        self.assertTrue(lines[1].endswith(",65,0,,,,,,"))

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
        self.assertTrue(lines[1].endswith(",174.476037,,,,,"))   # chips, hot_peak, hot_level, chip_avg, watts_dc padded
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
        # 0.8.0 appends watts_dc after chip_avg (append-only: a new column always lands at the true end,
        # so this 0.7.0 snapshot of "the last four" necessarily grows by one here).
        self.assertEqual(poller.COLUMNS[-5:], ["chips", "hot_peak", "hot_level", "chip_avg", "watts_dc"])

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
        self.assertTrue(lines[1].endswith(",0.1:5/0,,,,"))       # hot_peak, hot_level, chip_avg, watts_dc padded
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
        # board_source="minerinfo": this test is about the plug, not board_source; "auto" would probe port
        # 4028 (closed on this fake) and add its own event line, which is not what this asserts against.
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, plug=self.plug,
                          events=self.events, board_source="minerinfo")
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
        # board_source="minerinfo": see test_watts_and_relay_read_from_the_plug -- this test is about the
        # plug's own transitions, not the unrelated port-4028 auto-probe.
        p = poller.Poller(api.Miner(self.fm.address, password="password"), self.csv, 30, plug=self.plug,
                          events=self.events, board_source="minerinfo")
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


class BoardSourceTest(unittest.TestCase):
    """gate2-pga-0.8.0 task 5, section B: sample(miner, source), the auto fallback and its one event line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def test_sample_source_4028_reads_the_sc5proii_boards(self):
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            m = api.Miner(fm.address, password="password")
            row = poller.sample(m, "4028", devs4028_port=fm.devs4028_port)
        self.assertEqual(len(row["_boards"]), 4)
        self.assertAlmostEqual(row["_boards"][0]["chip_temp"], 90.0)
        # the 4028 capture's own voltage/current, not the minerinfo capture's (3042.4275): the two captures
        # were taken a little apart in time, so the two transports' watts_dc legitimately differ a little.
        self.assertAlmostEqual(row["watts_dc"], 3036.905, delta=1)

    def test_sample_source_minerinfo_reads_the_sc5proii_boards(self):
        with FakeMiner(fixtures="sc5proii", dbg_locked_icinfo=True) as fm:
            m = api.Miner(fm.address, password="password")
            row = poller.sample(m, "minerinfo")
        self.assertEqual(len(row["_boards"]), 4)
        self.assertAlmostEqual(row["_boards"][0]["chip_temp"], 89.0)

    def test_auto_uses_4028_when_it_answers(self):
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            events = EventLog(Path(self.tmp.name) / "events.log")
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, events=events,
                              devs4028_port=fm.devs4028_port)
            row = p.poll_once()
        self.assertEqual(row["http"], "ok")
        self.assertEqual(p._source, "4028")
        self.assertAlmostEqual(row["_boards"][0]["chip_temp"], 90.0)     # the 4028 value, not minerinfo's 89.0
        self.assertNotIn("port 4028", "".join(events.tail()))

    def test_auto_falls_back_to_minerinfo_and_logs_once(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
        s.close()                              # a definitely-closed port: guaranteed connection refused
        with FakeMiner(fixtures="sc5proii", dbg_locked_icinfo=True) as fm:
            events = EventLog(Path(self.tmp.name) / "events.log")
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, events=events,
                              devs4028_port=closed_port)
            row = p.poll_once()
            row2 = p.poll_once()
        self.assertEqual(row["http"], "ok")
        self.assertEqual(p._source, "minerinfo")
        self.assertAlmostEqual(row["_boards"][0]["chip_temp"], 89.0)
        lines = [l for l in events.tail() if "port 4028 closed or silent" in l]
        self.assertEqual(len(lines), 1)              # once, not once per sample

    def test_forced_minerinfo_ignores_an_available_4028(self):
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, board_source="minerinfo",
                              devs4028_port=fm.devs4028_port)
            row = p.poll_once()
        self.assertEqual(p._source, "minerinfo")
        self.assertAlmostEqual(row["_boards"][0]["chip_temp"], 89.0)


class IcinfoLockedTest(unittest.TestCase):
    """A persistent 401 on /dbg/icinfo no longer fails the sample (section B, task 5)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def rows(self):
        with open(self.csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_locked_icinfo_leaves_chip_columns_blank_and_logs_once(self):
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            events = EventLog(Path(self.tmp.name) / "events.log")
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, events=events,
                              devs4028_port=fm.devs4028_port)
            p.poll_once()
            p.poll_once()
        self.assertEqual(p.errors, 0)
        r = self.rows()
        self.assertEqual(r[0]["http"], "ok")
        self.assertEqual(r[0]["chips"], "")
        self.assertEqual(r[0]["weak_chips"], "")
        lines = [l for l in events.tail() if "icinfo" in l]
        self.assertEqual(len(lines), 1)               # once, not once per sample

    def test_icinfo_that_never_answers_costs_the_chip_columns_not_the_row(self):
        # The sc5proii folder has no icinfo capture, so the fake answers 404: a unit without the endpoint.
        with FakeMiner(fixtures="sc5proii", port4028=True) as fm:
            events = EventLog(Path(self.tmp.name) / "events.log")
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, events=events,
                              devs4028_port=fm.devs4028_port)
            row = p.poll_once()
            p.poll_once()
        self.assertEqual(p.errors, 0)
        self.assertEqual(len(row["_boards"]), 4)
        r = self.rows()
        self.assertEqual([x["http"] for x in r], ["ok", "ok"])
        self.assertEqual(r[0]["chips"], "")
        lines = [l for l in events.tail() if "icinfo" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("HTTP 404", lines[0])

    def test_icinfo_failing_after_it_has_answered_is_still_an_error(self):
        # The SC-BOX path: once icinfo has answered in this run, a failure is a failed sample, as it always
        # was. A blank row here would put a zero in nonces_good: series.inc reads that as a counter reset and
        # adds the run's whole count again on the next row, and the trials' first-to-last difference breaks.
        with FakeMiner() as fm:
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30)
            p.poll_once()
            fm.icinfo = None                          # the fake now answers 404 on /dbg/icinfo
            p.poll_once()
        self.assertEqual(p.errors, 1)
        r = self.rows()
        self.assertEqual(r[0]["http"], "ok")
        self.assertTrue(r[1]["http"].startswith("ERR"), r[1]["http"])


class BoardsCsvTest(unittest.TestCase):
    """boards.csv beside log.csv: written only for more than one board (section B, task 5)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"
        self.boards_csv = Path(self.tmp.name) / "boards.csv"

    def test_written_for_the_sc5proii_fake(self):
        with FakeMiner(fixtures="sc5proii", port4028=True, dbg_locked_icinfo=True) as fm:
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, board_source="4028",
                              devs4028_port=fm.devs4028_port)
            p.poll_once()
        self.assertTrue(self.boards_csv.exists())
        with open(self.boards_csv, newline="", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.BOARDS_COLUMNS))
        self.assertEqual(len(lines), 5)                # header + 4 boards
        with open(self.boards_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["board"] for r in rows], ["0", "1", "2", "3"])
        self.assertEqual(rows[0]["tstemp0"], "90.0")

    def test_absent_for_the_sc_box_fake(self):
        with FakeMiner(port4028=True) as fm:          # default fixtures: the SC-BOX, one board
            p = poller.Poller(api.Miner(fm.address, password="password"), self.csv, 30, board_source="4028",
                              devs4028_port=fm.devs4028_port)
            row = p.poll_once()
        self.assertEqual(row["http"], "ok")
        self.assertEqual(len(row["_boards"]), 1)
        self.assertFalse(self.boards_csv.exists())


class WattsDcMigrationTest(unittest.TestCase):
    """`watts_dc` is the new last log column; an older header is migrated in place (section B, task 5).

    The header and row below are a literal copy of this machine's own ~/.gbox/log.csv (2026-09-15,
    before this change), written only into this test's own tempfile.TemporaryDirectory. Nothing here
    opens the live file.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "log.csv"

    def test_migrates_a_copy_of_a_real_log_without_touching_the_live_one(self):
        old_header = ("time,http,elapsed,mhs_av,mhs_20s,hwerr,hwerr_pct,accepted,rejected,clock,fan0,fan1,"
                     "tstemp0,tstemp1,tstemp2,rebootcnt,weak_chips,nonces_good,nonces_bad,temp_target,overheat,"
                     "watts,chips,hot_peak,hot_level,chip_avg")
        self.assertEqual(old_header, ",".join(poller.COLUMNS[:-1]))
        row1 = ("2026-09-05 21:05:52,ok,38859,714194.349,761630.09,2912,2.6095,17780,4713,600.0,3120,3060,73.0,"
               "73.0,64.63,355,3:6990/90;8:4481/2386;9:7032/51;10:7067/103;15:7030/120;16:7174/72,,,,,,,,,")
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(old_header + "\n" + row1 + "\n")
        self.assertTrue(poller.migrate_columns(self.csv))
        self.assertFalse(poller.migrate_columns(self.csv))       # already current: a no-op
        with open(self.csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(poller.COLUMNS))
        self.assertEqual(lines[0].split(",")[-1], "watts_dc")
        self.assertEqual(lines[1].count(","), len(poller.COLUMNS) - 1)
        self.assertTrue(lines[1].endswith(","))                  # watts_dc padded blank
        self.assertTrue((self.csv.parent / "log.csv.bak").exists())


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

    def test_the_two_post_cycle_timers_are_bounded(self):
        """Both are measured from power returning, and both have a floor of 2 minutes for different reasons.

        settle_minutes had no bound at all, and it is the one that suppresses the stall check: at 20 it hid a
        dead hashboard for 22 minutes (2026-09-17), so a typo of 600 would hide one for ten hours. Below 2 it
        judges a miner that is still booting -- measured boot is 60 to 66 s.

        boot_check_minutes rejects 1 specifically. Every one of the five known cold-start failures drew over
        boot_watts transiently in its first 30 to 70 seconds before collapsing to 2-10 W, so a reading that
        early sees the spike and concludes the box booted fine -- missing the failure it exists to catch."""
        power = config.Config(host="h", power={"host": "p"}).power
        self.assertEqual((power["settle_minutes"], power["boot_check_minutes"]), (6, 2))
        config.Config(host="h", power={"host": "p", "settle_minutes": 2}).validate()
        config.Config(host="h", power={"host": "p", "settle_minutes": 60}).validate()
        for bad in (1, 0, -1, 61):
            with self.assertRaises(ValueError):
                config.Config(host="h", power={"host": "p", "settle_minutes": bad}).validate()
        config.Config(host="h", power={"host": "p", "boot_check_minutes": 0}).validate()   # 0 switches it off
        config.Config(host="h", power={"host": "p", "boot_check_minutes": 2}).validate()
        for bad in (1, 11, -1):
            with self.assertRaises(ValueError):
                config.Config(host="h", power={"host": "p", "boot_check_minutes": bad}).validate()

    def test_validate_rejects_fast_polling(self):
        with self.assertRaises(ValueError):
            config.Config(poll_interval=5).validate()
        config.Config(poll_interval=10).validate()

    def test_board_source_default_round_trip_and_validate(self):
        cfg = config.load(self.tmp.name)
        self.assertEqual(cfg.board_source, "auto")
        config.save(config.Config(host="h", board_source="4028"), self.tmp.name)
        back = config.load(self.tmp.name)
        self.assertEqual(back.board_source, "4028")
        back.validate()
        with self.assertRaises(ValueError):
            config.Config(board_source="devs").validate()

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


class RotateTest(unittest.TestCase):
    """`rotate`: cap a growing log, keep the recent rows in place, archive the whole old file as .1.

    Nothing here touches ~/.gbox. Every test works in a temp directory and passes an explicit `now`,
    so no test depends on the clock.
    """

    STAMP = "%Y-%m-%d %H:%M:%S"
    LF = chr(10)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.csv = self.data / "log.csv"
        self.archive = self.data / "log.csv.1"
        self.carried = self.data / "log.csv.tmp"
        self.now = datetime.datetime(2026, 9, 19, 12, 0, 0)

    def stamp(self, age_hours):
        return (self.now - datetime.timedelta(hours=age_hours)).strftime(self.STAMP)

    def write_log(self, ages, header=None, junk=()):
        """A log with one row per age in `ages` (hours before `self.now`), plus any `junk` lines verbatim."""
        head = header if header is not None else ",".join(poller.COLUMNS)
        blanks = "," * (len(head.split(",")) - 1)
        lines = [head] + [self.stamp(a) + blanks for a in ages] + list(junk)
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(self.LF.join(lines) + self.LF)
        return self.csv.read_bytes()

    def body(self, path):
        with open(path, encoding="utf-8", newline="") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def test_rows_inside_the_window_are_carried_and_older_ones_are_not(self):
        self.write_log([100, 80, 71, 10, 0.5])
        self.assertTrue(poller.rotate(self.csv, 72, now=self.now))
        rows = self.body(self.csv)[1:]
        self.assertEqual([r.split(",")[0] for r in rows], [self.stamp(71), self.stamp(10), self.stamp(0.5)])

    def test_a_little_more_than_the_window_is_carried_so_an_edge_reader_sees_whole_buckets(self):
        """Carrying exactly keep_hours leaves the oldest bucket of a keep_hours reader half full.

        Measured on a copy of the real 8.5 MB log: of 145 half-hour buckets over 72 h, exactly one
        differed across a rotation, the oldest, 29 samples where it had been 60. The three-day errors
        chart asks for exactly 72 h, so the margin is what keeps a rotation invisible.
        """
        margin = poller.CARRY_MARGIN_HOURS
        self.assertGreater(margin, 0)
        self.write_log([72 + margin + 1, 72 + margin - 0.5, 1])
        poller.rotate(self.csv, 72, now=self.now)
        kept = [r.split(",")[0] for r in self.body(self.csv)[1:]]
        self.assertEqual(kept, [self.stamp(72 + margin - 0.5), self.stamp(1)])

    def test_the_header_is_kept_verbatim_and_not_rewritten_to_the_current_columns(self):
        self.write_log([1], header="time,http,elapsed")
        poller.rotate(self.csv, 72, now=self.now)
        self.assertEqual(self.body(self.csv)[0], "time,http,elapsed")

    def test_the_whole_old_file_is_archived_byte_for_byte(self):
        before = self.write_log([100, 1])
        poller.rotate(self.csv, 72, now=self.now)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_an_archive_from_an_earlier_rotation_is_replaced(self):
        self.archive.write_bytes(b"an older archive")
        before = self.write_log([1])
        poller.rotate(self.csv, 72, now=self.now)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_a_row_whose_time_cannot_be_read_is_dropped_from_the_carry_but_kept_in_the_archive(self):
        self.write_log([1], junk=["not-a-time,,,", ",,,", "   "])
        poller.rotate(self.csv, 72, now=self.now)
        self.assertEqual([r.split(",")[0] for r in self.body(self.csv)[1:]], [self.stamp(1)])
        self.assertIn("not-a-time", self.archive.read_text(encoding="utf-8"))

    def test_a_window_that_holds_nothing_leaves_a_header_and_still_archives(self):
        before = self.write_log([100, 90])
        poller.rotate(self.csv, 72, now=self.now)
        self.assertEqual(len(self.body(self.csv)), 1)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_no_working_file_is_left_behind(self):
        self.write_log([1])
        poller.rotate(self.csv, 72, now=self.now)
        self.assertFalse(self.carried.exists())

    def test_the_note_names_the_row_count_the_window_and_the_archive(self):
        self.write_log([100, 100, 1])
        note = poller.rotate(self.csv, 72, now=self.now)
        self.assertIn("log.csv.1", note)
        self.assertIn("1 row", note)
        self.assertIn("72", note)

    def test_a_missing_file_is_nothing_to_do(self):
        self.assertFalse(poller.rotate(self.csv, 72, now=self.now))

    def test_an_empty_file_is_nothing_to_do(self):
        self.csv.write_bytes(b"")
        self.assertFalse(poller.rotate(self.csv, 72, now=self.now))

    def test_an_os_error_leaves_every_file_exactly_as_it_was(self):
        """A reader holding log.csv open is a PermissionError on Windows. Standing a directory in the
        working file's place is the portable way to provoke the same class of failure."""
        before = self.write_log([100, 1])
        self.carried.mkdir()
        note = poller.rotate(self.csv, 72, now=self.now)
        self.assertIn("deferred", note)
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertFalse(self.archive.exists())

    def test_a_rotation_interrupted_between_the_two_moves_is_finished_at_start(self):
        """The crash window: log.csv has become .1 and the carried rows are still in .tmp."""
        self.write_log([100, 1])
        poller.rotate(self.csv, 72, now=self.now)
        carried = self.csv.read_bytes()
        self.csv.replace(self.carried)              # back into the crash state
        self.assertFalse(self.csv.exists())
        note = poller.migrate_columns(self.csv)
        self.assertTrue(note)
        self.assertEqual(self.csv.read_bytes(), carried)
        self.assertFalse(self.carried.exists())

    def test_a_working_file_beside_a_healthy_log_is_left_alone(self):
        self.write_log([1])
        self.carried.write_bytes(b"stale")
        poller.migrate_columns(self.csv)
        self.assertEqual(self.carried.read_bytes(), b"stale")
