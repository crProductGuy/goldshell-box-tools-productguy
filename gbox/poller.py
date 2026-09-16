"""The poll loop: one sample per interval into log.csv, then the watchdog.

One thread, three requests per sample (minerinfo, icinfo, setting), through
the serialized session. The columns are what the dashboard's fan and
temperature chart and the clock-trials table read by header name, so adding
a column is safe and renaming one is not. Columns are only ever appended;
`migrate_columns` brings an older log up to date on service start.

With a smart plug configured, the plug's relay and meter are read after the
miner sample (never concurrently with it) and the watts go in the last
column, empty when the plug has no meter or did not answer. A plug outage
never fails a sample; it is one event line on the way out and one on the
way back.

Every `syslog_interval` seconds (0.7.0) the cycle makes one more serialized
request after the sample: the cgminer log, where the hottest chip's
temperature lives (no other endpoint carries it). From the lines newer than
the last read it writes the hottest chip's peak, its sustained level (the
median of the 5-second readings) and the chip average as the last three
columns; every other row leaves them blank, like `watts` without a plug. The
log text itself is never stored or logged: it repeats the pool user.

0.8.0: `board_source` ("auto", "4028" or "minerinfo") picks the transport for
every board of a multi-board unit. "auto" probes port 4028 once, on the
first sample, and falls back to `/dbg/minerinfo` on a `MinerError` there,
with one event line; the resolved transport then holds for the life of the
poller. The per-board list rides the row under `_boards` (a key `COLUMNS`
does not list, so `_append` ignores it) and is written to `boards.csv`
beside `log.csv`, one row per board per poll, only when the unit has more
than one board. `watts_dc` (the firmware's own voltage x current, DC side)
is the last log column. A persistent 401 on `/dbg/icinfo` (`AuthError`) no
longer fails the sample: the chip-level columns go blank and one event line
says so, once.
"""
import datetime
import shutil
import statistics
import threading
import time
from pathlib import Path

from . import api

COLUMNS = ["time", "http", "elapsed", "mhs_av", "mhs_20s", "hwerr", "hwerr_pct", "accepted",
           "rejected", "clock", "fan0", "fan1", "tstemp0", "tstemp1", "tstemp2", "rebootcnt",
           "weak_chips", "nonces_good", "nonces_bad", "temp_target", "overheat", "watts",
           "chips",        # 0.6.0: every chip's cumulative good/bad as board.chip:g/b;... (docs/charts-proposal.md)
           "hot_peak", "hot_level", "chip_avg",   # 0.7.0: from the cgminer log, on the rows where it was read
           "watts_dc"]     # 0.8.0: the firmware's own voltage x current (DC side); None when either is unknown
FIRST_READ_MINUTES = 5        # the first log read after a service start looks back this far only, by the miner's clock

# boards.csv (0.8.0): one row per board per poll, beside log.csv, only when the unit has more than one board.
# A second file rather than widening log.csv, because a variable board count does not fit one append-only header.
BOARDS_COLUMNS = ["time", "board", "elapsed", "mhs_20s", "mhs_av", "accepted", "rejected", "hwerr", "hwerr_pct",
                  "clock", "tstemp0", "tstemp2", "rebootcnt", "overheat"]

BOARD_SOURCES = ("auto", "4028", "minerinfo")


def sample(miner, source, devs4028_port=4028):
    """One sample as a dict keyed by COLUMNS. Raises MinerError on failure.

    `source` picks the transport for the per-board data: "4028" reads cgminer-style port 4028
    (`Miner.devs4028`), anything else reads `/dbg/minerinfo` (`Miner.minerinfo_boards`). `devs4028_port`
    lets tests point port 4028 at a fake miner's listener; production always uses the real 4028.

    Two extra keys ride along under names `COLUMNS` does not list, so `_append` ignores them:
    `_boards` (the per-board list, for `boards.csv` and `/api/boards`) and `_icinfo_locked` (True when
    `/dbg/icinfo`'s 401 persisted this sample, so the chip-level columns are blank rather than losing the
    whole row -- unproven on the SC5 Pro II).
    """
    boards = miner.devs4028(port=devs4028_port) if source == "4028" else miner.minerinfo_boards()
    info = api.board_totals(boards)
    try:
        chip_boards = miner.boards()
        icinfo_locked = False
    except api.AuthError:
        chip_boards = []
        icinfo_locked = True
    setting = miner.setting()
    weak = ";".join("%d:%d/%d" % (c["chip"], c["good"], c["bad"])
                    for board in chip_boards for c in api.weak_chips(board))
    chips = [c for board in chip_boards for c in board]
    return {
        "http": "ok", "elapsed": info["elapsed"], "mhs_av": info["mhs_av"], "mhs_20s": info["mhs_20s"],
        "hwerr": info["hw_errors"], "hwerr_pct": info["hw_pct"], "accepted": info["accepted"],
        "rejected": info["rejected"], "clock": info["clock"], "fan0": info["fan0"], "fan1": info["fan1"],
        "tstemp0": info["chip_temp"], "tstemp1": info["chip_temp1"], "tstemp2": info["board_temp"],
        "rebootcnt": info["rebootcnt"], "weak_chips": weak,
        "nonces_good": sum(c["good"] for c in chips), "nonces_bad": sum(c["bad"] for c in chips),
        "temp_target": setting.get("temp_target"), "overheat": info["overheat"],
        "chips": api.format_chips(chip_boards),
        "watts_dc": info["watts_dc"], "_boards": boards, "_icinfo_locked": icinfo_locked,
    }


def summarize_chiptemps(readings):
    """The three log columns from (miner_ts, avg, max) tuples: the peak, the median max (the sustained level),
    the median average. None everywhere when there are no readings."""
    if not readings:
        return None, None, None
    maxes = [r[2] for r in readings]
    return max(maxes), float(statistics.median(maxes)), float(statistics.median(r[1] for r in readings))


def migrate_columns(csv_path):
    """Bring a log written with an older, shorter header up to COLUMNS in place.

    Old rows are padded with empty fields so the header and every row agree.
    The original is copied to log.csv.bak first (once; a later migration
    does not overwrite an older backup). Returns a short note for the event
    log when the file changed, saying whether the backup is new or an older
    one was kept, and False otherwise. A missing file, a current header, or
    a header this code does not know are left alone.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        return False
    with open(csv_path, encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n")
        old = header.split(",") if header else []
        if not old or old == COLUMNS or COLUMNS[:len(old)] != old or len(old) >= len(COLUMNS):
            return False
        body = f.read()
    pad = "," * (len(COLUMNS) - len(old))
    backup = csv_path.with_name(csv_path.name + ".bak")
    if backup.exists():
        note = "the older %s was left as is" % backup.name
    else:
        shutil.copyfile(csv_path, backup)
        note = "copy kept as %s" % backup.name
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(COLUMNS) + "\n")
        for line in body.splitlines():
            if line:
                f.write(line + pad + "\n")
    tmp.replace(csv_path)
    return note


def error_row(exc):
    return {"http": "ERR:%s:%s" % (type(exc).__name__, str(exc)[:60].replace(",", ";").replace("\n", " "))}


class Poller(threading.Thread):
    def __init__(self, miner, csv_path, interval, watchdog=None, events=None, clock=time.time, plug=None, scheduler=None,
                 syslog_interval=0, board_source="auto", devs4028_port=4028):
        super().__init__(name="gbox-poller", daemon=True)
        self.miner = miner
        self.syslog_interval = float(syslog_interval or 0)   # 0: never read the cgminer log
        self.syslog_errors = 0
        self._syslog_next = None    # clock time of the next log read; None means at the next good sample
        self._syslog_cursor = None  # the newest miner timestamp seen, so a read takes only newer lines
        self.scheduler = scheduler  # gbox.power.Scheduler: ticked once per sample, after the watchdog
        self.csv_path = Path(csv_path)
        self.boards_csv_path = self.csv_path.with_name("boards.csv")
        self.interval = float(interval)
        self.watchdog = watchdog
        self.events = events
        self._clock = clock
        self._stop = threading.Event()
        self.latest = None          # last row written, as a dict, with "time"
        self.samples = 0
        self.errors = 0
        self.last_error = None
        self.plug = plug
        self.plug_info = None       # identify() once it answered: model, alias, meter
        self.plug_state = None      # True on, False off, None unknown or no plug
        self.plug_watts = None
        self._plug_down = False
        self.miner_status = None    # /mcb/status (model, firmware, hardware), read once after the first good sample
        if board_source not in BOARD_SOURCES:
            raise ValueError("board_source must be one of: %s" % ", ".join(BOARD_SOURCES))
        self.board_source = board_source        # "auto", "4028" or "minerinfo"
        self.devs4028_port = devs4028_port
        self._source = None if board_source == "auto" else board_source   # resolved transport, once decided
        self._icinfo_locked = False   # one event line the first time /dbg/icinfo's 401 persists, never again

    def _sample(self):
        """One sample via the transport `board_source` picks. "auto": the very first sample probes port
        4028 directly; a `MinerError` there (never `NoCredentials` -- port 4028 needs no token, so a
        missing one says nothing about the socket) falls back to `/dbg/minerinfo` for the rest of the
        run, with one event line. Once resolved, by success or by fallback, the source never changes
        again for this poller."""
        if self._source is not None:
            return sample(self.miner, self._source, devs4028_port=self.devs4028_port)
        try:
            self.miner.devs4028(port=self.devs4028_port)   # probe; sample() below reads it again, once
        except api.MinerError:
            self._source = "minerinfo"
            if self.events:
                self.events.write("miner: port 4028 closed or silent; reading boards from /dbg/minerinfo")
            return sample(self.miner, "minerinfo")
        self._source = "4028"
        return sample(self.miner, "4028", devs4028_port=self.devs4028_port)

    def _append_boards(self, now, boards):
        """boards.csv beside log.csv: one row per board per poll, header on create. Only called when the
        unit has more than one board; a single-board unit never gets this file."""
        new = not self.boards_csv_path.exists() or self.boards_csv_path.stat().st_size == 0
        with open(self.boards_csv_path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(",".join(BOARDS_COLUMNS) + "\n")
            for b in boards:
                row = {"time": now, "board": b["board"], "elapsed": b["elapsed"], "mhs_20s": b["mhs_20s"],
                       "mhs_av": b["mhs_av"], "accepted": b["accepted"], "rejected": b["rejected"],
                       "hwerr": b["hw_errors"], "hwerr_pct": b["hw_pct"], "clock": b["clock"],
                       "tstemp0": b["chip_temp"], "tstemp2": b["board_temp"], "rebootcnt": b["rebootcnt"],
                       "overheat": b["overheat"]}
                f.write(",".join("" if row.get(c) is None else str(row.get(c)) for c in BOARDS_COLUMNS) + "\n")

    def read_plug(self):
        """Relay and watts from the plug into plug_state and plug_watts; one event line per transition."""
        if self.plug is None:
            return
        try:
            if self.plug_info is None:
                self.plug_info = self.plug.identify()
            self.plug_state = self.plug.state()
            self.plug_watts = self.plug.watts() if self.plug_info.get("meter") else None
            if self._plug_down:
                self._plug_down = False
                if self.events:
                    self.events.write("power: plug back")
        except Exception as e:
            self.plug_state = None
            self.plug_watts = None
            if not self._plug_down:
                self._plug_down = True
                if self.events:
                    self.events.write("power: plug unreachable (%s); the watchdog cannot cycle until it answers" % e)

    def read_chiptemps(self):
        """The hottest-chip columns for this row: (peak, level, chip_avg) from one log read when one is due,
        else (None, None, None). A failed read is a blank and a count, never an error row."""
        now = self._clock()
        if self.syslog_interval <= 0 or (self._syslog_next is not None and now < self._syslog_next):
            return None, None, None
        self._syslog_next = now + self.syslog_interval
        try:
            text = self.miner.syslog()
            readings = api.parse_chiptemps(text, after=self._syslog_cursor)
            boot = api.last_boot_ts(text)
        except Exception:
            self.syslog_errors += 1
            return None, None, None
        if boot is not None and (self._syslog_cursor is None or boot > self._syslog_cursor):
            # the log survives a power cycle (2026-09-15 07:07: a row 18 s after a boot carried the run before the
            # freeze); readings written before the newest boot line are the old run's, not this one's
            readings = [r for r in readings if r[0] > boot]
            if not readings:
                self._syslog_cursor = boot
        if not readings:
            return None, None, None
        if self._syslog_cursor is None:     # first read: the last few minutes only, by the miner's own clock
            last = datetime.datetime.strptime(readings[-1][0], "%Y-%m-%d %H:%M:%S")
            floor = (last - datetime.timedelta(minutes=FIRST_READ_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            readings = [r for r in readings if r[0] >= floor]
        self._syslog_cursor = readings[-1][0]
        return summarize_chiptemps(readings)

    def stop(self):
        self._stop.set()

    def poll_once(self):
        """Take one sample, append it, feed the watchdog. Returns the row."""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            row = self._sample()
            self.samples += 1
            self.last_error = None
            if row.get("_icinfo_locked") and not self._icinfo_locked:
                self._icinfo_locked = True
                if self.events:
                    self.events.write("miner: /dbg/icinfo 401 persisted; chip-level columns blank until it answers")
            if self.miner_status is None:            # one extra request, once, after the sample (never concurrent)
                try:
                    self.miner_status = self.miner.status()
                except Exception:
                    pass
            row["hot_peak"], row["hot_level"], row["chip_avg"] = self.read_chiptemps()   # a fourth request, when due
        except api.NoCredentials:
            row = {"http": "ERR:NoCredentials:waiting for a token from the dashboard"}
            self.errors += 1
            self.last_error = row["http"]
        except Exception as e:
            row = error_row(e)
            self.errors += 1
            self.last_error = row["http"]
        row["time"] = now
        self.read_plug()
        row["watts"] = self.plug_watts
        self._append(row)
        boards = row.get("_boards")
        if boards and len(boards) > 1:
            self._append_boards(now, boards)
        self.latest = row
        if self.watchdog is not None and not row["http"].startswith("ERR:NoCredentials"):
            ok = row["http"] == "ok"
            # hashing: the board reported a 20 s hashrate. A controller back from a power-on without its
            # hashboard answers with 0.0 (2026-09-13 15:46), and a hold must not release on that.
            self.watchdog.observe(ok, row.get("accepted"), self._clock(), hashing=ok and (row.get("mhs_20s") or 0) > 0)
            self.watchdog.check()
        if self.scheduler is not None:
            try:
                self.scheduler.tick()
            except Exception as e:          # a schedule bug must never cost a sample
                if self.events:
                    self.events.write("service: schedule tick failed: %s" % e)
        return row

    def _append(self, row):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(",".join(COLUMNS) + "\n")
            f.write(",".join("" if row.get(c) is None else str(row.get(c)) for c in COLUMNS) + "\n")

    def run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            self.poll_once()
            elapsed = time.monotonic() - started
            self._stop.wait(max(1.0, self.interval - elapsed))
