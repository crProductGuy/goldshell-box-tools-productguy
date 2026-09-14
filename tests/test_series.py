"""gbox.series: the log bucketed for the charts, with the counter-reset rule, the worst chip, and events."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from gbox import poller, series


def T(h, m, d=13):
    return dt.datetime(2026, 9, d, h, m, 0)


def row(t, ok=True, good=None, bad=None, resets=None, chips=None, weak=None, mhs=700000.0, clock=550.0, watts=190.0,
        fan=1260.0, chip_t=70.0, board_t=61.0):
    """A parsed row as series.read_rows returns it."""
    r = {"t": t, "ok": ok, "mhs_20s": mhs if ok else None, "clock": clock if ok else None, "watts": watts,
         "fan0": fan if ok else None, "fan1": fan if ok else None, "tstemp0": chip_t if ok else None, "tstemp2": board_t if ok else None,
         "nonces_good": good if ok else None, "nonces_bad": bad if ok else None, "rebootcnt": resets if ok else None,
         "hwerr": bad if ok else None, "accepted": None, "elapsed": None, "chips": chips, "weak": weak or {}}
    return r


def chips(good8, bad8, good1):
    return {"0.1": (good1, 0), "0.8": (good8, bad8)}


class IncrementTest(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(series.inc(100, 130), 30)
        self.assertEqual(series.inc(130, 20), 20)           # the counter restarted: the new value is the increment
        self.assertEqual(series.inc(5, 5), 0)
        self.assertIsNone(series.inc(None, 5))
        self.assertIsNone(series.inc(5, None))


class BucketsTest(unittest.TestCase):
    """Two hours, 30-minute buckets, a reset burst at 10:40 to 11:00, a boot at 11:10, an empty last bucket."""

    def rows(self):
        out = []
        good, bad, rb, g8, b8, g1 = 1000, 10, 0, 500, 5, 500
        for minutes in range(-10, 61, 10):                    # 09:50 .. 11:00, cumulative and growing
            t = T(9, 50) + dt.timedelta(minutes=minutes + 10)
            if t >= T(10, 50):
                rb = 3 if t == T(10, 50) else rb             # the burst: +3 between 10:40 and 10:50
            out.append(row(t, good=good, bad=bad, resets=rb, chips=chips(g8, b8, g1)))
            good += 100; bad += 1; g8 += 50; b8 += 1; g1 += 50
        out.append(row(T(11, 10), good=50, bad=0, resets=0, chips=chips(25, 0, 25)))       # boot: everything fell
        out.append(row(T(11, 20), good=150, bad=1, resets=2, chips=chips(75, 1, 75)))
        out.append(row(T(11, 25), ok=False))                                                 # one failed sample
        return out

    def test_shape_and_alignment(self):
        r = series.buckets(self.rows(), hours=2, bucket_minutes=30, now=T(12, 7))
        self.assertEqual(r["bucket_minutes"], 30)
        self.assertEqual([b["t"] for b in r["buckets"]], ["2026-09-13 10:00", "2026-09-13 10:30", "2026-09-13 11:00", "2026-09-13 11:30", "2026-09-13 12:00"])
        self.assertEqual(r["from"], "2026-09-13 10:00")
        self.assertEqual(r["to"], "2026-09-13 12:07")

    def test_counts_share_and_resets_sum_increments_with_the_reset_rule(self):
        b = {x["t"][11:]: x for x in series.buckets(self.rows(), 2, 30, now=T(12, 7))["buckets"]}
        # 10:30 bucket: rows 10:40, 10:50, 11:00 each add 100 good and 1 bad; the burst adds 3 resets
        self.assertEqual((b["10:30"]["good"], b["10:30"]["bad"], b["10:30"]["resets"]), (300, 3, 3))
        self.assertAlmostEqual(b["10:30"]["share"], 100 * 3 / 303, places=3)
        # 11:00 bucket: 10:50 -> 11:00 adds 100/1/0 (an increment counts in the bucket of the later sample);
        # 11:00 -> 11:10 the counters fell (boot), so the new values 50/0/0 count; 11:10 -> 11:20 adds 100/1/2
        self.assertEqual((b["11:00"]["good"], b["11:00"]["bad"], b["11:00"]["resets"]), (250, 2, 2))
        self.assertEqual(b["11:00"]["samples"], 3)
        self.assertEqual(b["11:00"]["errors"], 1)

    def test_worst_chip_from_the_chips_column(self):
        b = {x["t"][11:]: x for x in series.buckets(self.rows(), 2, 30, now=T(12, 7))["buckets"]}
        w = b["10:30"]["worst"]
        self.assertEqual(w["chip"], "0.8")
        self.assertEqual((w["good"], w["bad"]), (150, 3))
        self.assertAlmostEqual(w["share"], 100 * 3 / 153, places=3)
        self.assertEqual(b["11:00"]["worst"]["chip"], "0.8")              # +50/+1, then 25/0 (fell), then +50/+1
        self.assertEqual((b["11:00"]["worst"]["good"], b["11:00"]["worst"]["bad"]), (125, 2))

    def test_worst_chip_falls_back_to_weak_chips_on_old_rows(self):
        rows = [row(T(10, 0), good=100, bad=1, resets=0, weak={"0.8": (50, 1)}),
                row(T(10, 10), good=200, bad=3, resets=0, weak={"0.8": (100, 3), "0.3": (90, 0)}),
                row(T(10, 20), good=300, bad=3, resets=0, weak={"0.3": (140, 0)})]      # chip 8 no longer flagged: no pair
        b = series.buckets(rows, 1, 30, now=T(10, 30))["buckets"]
        w = [x for x in b if x["t"].endswith("10:00")][0]["worst"]
        self.assertEqual(w["chip"], "0.8")
        self.assertEqual((w["good"], w["bad"]), (50, 2))

    def test_empty_bucket_is_nulls_not_zeros(self):
        b = {x["t"][11:]: x for x in series.buckets(self.rows(), 2, 30, now=T(12, 7))["buckets"]}
        e = b["11:30"]
        self.assertEqual(e["samples"], 0)
        for k in ("hashrate", "fan0", "chip_temp", "watts", "clock", "good", "bad", "share", "resets", "worst"):
            self.assertIsNone(e[k], k)

    def test_means_and_clock(self):
        b = {x["t"][11:]: x for x in series.buckets(self.rows(), 2, 30, now=T(12, 7))["buckets"]}
        self.assertAlmostEqual(b["10:30"]["hashrate"], 700000.0)
        self.assertAlmostEqual(b["10:30"]["watts"], 190.0)
        self.assertAlmostEqual(b["10:30"]["chip_temp"], 70.0)
        self.assertAlmostEqual(b["10:30"]["board_temp"], 61.0)
        self.assertEqual(b["10:30"]["clock"], 550.0)
        self.assertIsNone(b["10:30"]["worst"] if False else None)

    def test_events_inside_the_window_only(self):
        lines = ["2026-09-13 09:00:00 power: cycled #1 today: x", "2026-09-13 10:41:00 watchdog: restart #1 sent (x)",
                 "2026-09-13 11:00:00 service: session token received from the dashboard", "2026-09-13 11:30:00 hold: started by you until x (y)",
                 "garbage"]
        r = series.buckets(self.rows(), 2, 30, now=T(12, 7), events=lines)
        self.assertEqual([e["t"] for e in r["events"]], ["2026-09-13 10:41:00", "2026-09-13 11:30:00"])
        self.assertTrue(r["events"][0]["label"].startswith("watchdog: restart"))

    def test_limits(self):
        with self.assertRaises(ValueError):
            series.buckets([], 0, 30)
        with self.assertRaises(ValueError):
            series.buckets([], 200, 30)
        with self.assertRaises(ValueError):
            series.buckets([], 24, 3)
        with self.assertRaises(ValueError):
            series.buckets([], 24, 200)


class ReadRowsTest(unittest.TestCase):
    def test_reads_ok_and_failed_rows_with_chips_and_weak(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.csv"
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(",".join(poller.COLUMNS) + "\n")
                f.write("2026-09-13 15:52:32,ok,5,13015.584,13015.584,0,0.0,1,0,550.0,2880,2880,34.0,34.0,24.63,0,8:10/2,10,0,65,0,174.476037,0.1:5/0;0.8:10/2\n")
                f.write("2026-09-13 15:53:02,ERR:MinerError:GET dbg/minerinfo: timed out,,,,,,,,,,,,,,,,,,,,26.1,\n")
                f.write("2026-09-13 15:53:32,ok,65,568103.755,568103.755,1,0.0,2,0,550.0,4500,4500,47.0,47.0,37.88,0,,159,1,65,0,175.7,\n")
            rows = series.read_rows(p)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]["ok"]); self.assertFalse(rows[1]["ok"]); self.assertTrue(rows[2]["ok"])
        self.assertEqual(rows[0]["chips"], {"0.1": (5, 0), "0.8": (10, 2)})
        self.assertEqual(rows[0]["weak"], {"0.8": (10, 2)})
        self.assertIsNone(rows[2]["chips"])                    # blank column (a row from before 0.6.0)
        self.assertEqual(rows[1]["watts"], 26.1)
        self.assertIsNone(rows[1]["nonces_good"])
        self.assertEqual(rows[2]["nonces_bad"], 1)
        self.assertEqual(series.read_rows(Path(d) / "missing.csv"), [])


class NoReadingTest(unittest.TestCase):
    """The firmware reports the board sensor as -150 and the chip as 0 when the hashboard is absent (2026-09-13 15:46);
    those are 'no reading', not temperatures, and must not drag a bucket's mean below zero."""

    def test_sensor_absent_values_read_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.csv"
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(",".join(poller.COLUMNS) + "\n")
                f.write("2026-09-13 15:47:02,ok,60,0.0,0.0,0,0.0,0,0,0.0,2340,2400,0.0,0.0,-150.0,0,,0,0,65,0,10.3,\n")
                f.write("2026-09-13 15:47:32,ok,90,0.0,0.0,0,0.0,0,0,0.0,2160,2100,0.0,0.0,-150.0,0,,0,0,65,0,9.9,\n")
                f.write("2026-09-13 15:48:02,ok,120,600000.0,600000.0,0,0.0,1,0,550.0,1980,1980,48.0,48.0,37.6,0,,10,0,65,0,190.0,\n")
            rows = series.read_rows(p)
        self.assertIsNone(rows[0]["tstemp2"])
        self.assertIsNone(rows[0]["tstemp0"])
        self.assertEqual(rows[2]["tstemp2"], 37.6)
        b = series.buckets(rows, 1, 5, now=T(15, 50))["buckets"]
        bucket = [x for x in b if x["t"].endswith("15:45")][0]
        self.assertAlmostEqual(bucket["board_temp"], 37.6)
        self.assertAlmostEqual(bucket["chip_temp"], 48.0)
        self.assertEqual(bucket["samples"], 3)


class FormatTableTest(unittest.TestCase):
    def test_header_and_a_row(self):
        r = series.buckets(BucketsTest().rows(), 2, 30, now=T(12, 7))
        text = series.format_table(r)
        head = text.splitlines()[0]
        for col in ("time", "clock", "bad share", "worst chip", "resets", "hashrate", "watts"):
            self.assertIn(col, head)
        self.assertIn("10:30", text)
        self.assertIn("0.8", text)


if __name__ == "__main__":
    unittest.main()
