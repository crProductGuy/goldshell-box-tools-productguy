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
and, since 0.9.0, `volts` (the board voltage exactly as the firmware reports
it, never converted) are the last log columns. A persistent 401 on `/dbg/icinfo` (`AuthError`) no
longer fails the sample: the chip-level columns go blank and one event line
says so, once.
"""
import datetime
import shutil
import statistics
import threading
import time
from pathlib import Path

from . import api, models

COLUMNS = ["time", "http", "elapsed", "mhs_av", "mhs_20s", "hwerr", "hwerr_pct", "accepted",
           "rejected", "clock", "fan0", "fan1", "tstemp0", "tstemp1", "tstemp2", "rebootcnt",
           "weak_chips", "nonces_good", "nonces_bad", "temp_target", "overheat", "watts",
           "chips",        # 0.6.0: every chip's cumulative good/bad as board.chip:g/b;... (docs/charts-proposal.md)
           "hot_peak", "hot_level", "chip_avg",   # 0.7.0: from the cgminer log, on the rows where it was read
           "watts_dc",     # 0.8.0: the firmware's own voltage x current (DC side); None when either is unknown
           "volts"]        # 0.9.0: the board voltage as the firmware reports it (0.41 on an SC-BOX over 4028, mV on
                           # an SC5 Pro II; never converted, the SC-BOX's unit is undocumented). 2026-09-20: 80 min at
                           # 162 W instead of 184 W with every chip producing, and nothing kept could say why.
FIRST_READ_MINUTES = 5        # the first log read after a service start looks back this far only, by the miner's clock

# boards.csv (0.8.0): one row per board per poll, beside log.csv, only when the unit has more than one board.
# A second file rather than widening log.csv, because a variable board count does not fit one append-only header.
BOARDS_COLUMNS = ["time", "board", "elapsed", "mhs_20s", "mhs_av", "accepted", "rejected", "hwerr", "hwerr_pct",
                  "clock", "tstemp0", "tstemp2", "rebootcnt", "overheat",
                  "volts"]    # 0.9.0, per board; an older boards.csv is migrated at start like log.csv

# minerlog.csv (0.9.0): what the miner's own log said, kept because the miner does not keep it (2026-09-20: 3.8 MB
# to 36 KB in two hours, with no restart). One row per label per log read; labels and counts only, see
# api.classify_syslog. Not rotated: a quiet miner writes nothing here. It stops growing at the cap instead, so a
# broken or hostile device cannot fill the disk through it.
MINERLOG_COLUMNS = ["time", "label", "count", "miner_first", "miner_last"]
MINERLOG_MAX_BYTES = 5 * 1024 * 1024

# The board-absent signature (0.9.0), for the watchdog. The firmware reports -150 for a board sensor it cannot
# read; every real reading on record is above 20. A mining process younger than this many seconds may simply not
# have found its board yet (measured boot: 5 to 25 s of its own uptime).
ABSENT_SENSOR_BELOW = -100
ABSENT_MIN_ELAPSED = 90

BOARD_SOURCES = ("auto", "4028", "minerinfo")


def sample(miner, source, devs4028_port=4028, icinfo_optional=False):
    """One sample as a dict keyed by COLUMNS. Raises MinerError on failure.

    `source` picks the transport for the per-board data: "4028" reads cgminer-style port 4028
    (`Miner.devs4028`), anything else reads `/dbg/minerinfo` (`Miner.minerinfo_boards`). `devs4028_port`
    lets tests point port 4028 at a fake miner's listener; production always uses the real 4028.

    Two extra keys ride along under names `COLUMNS` does not list, so `_append` ignores them:
    `_boards` (the per-board list, for `boards.csv` and `/api/boards`) and `_icinfo_locked` (True when
    `/dbg/icinfo`'s 401 persisted this sample, so the chip-level columns are blank rather than losing the
    whole row -- unproven on the SC5 Pro II). With `icinfo_optional`, an HTTP error status (a 404 or 500 from
    a model without the endpoint) is taken the same way and `_icinfo_locked` holds the status as text; the
    Poller allows that only until icinfo has answered once, so on a unit that has it a failure stays a
    failed sample. A miner that does not answer at all is never tolerated here.
    """
    boards = miner.devs4028(port=devs4028_port) if source == "4028" else miner.minerinfo_boards()
    info = api.board_totals(boards)
    try:
        chip_boards = miner.boards()
        icinfo_locked = False
    except api.AuthError:
        chip_boards = []
        icinfo_locked = True
    except api.HttpStatusError as e:
        if not icinfo_optional:
            raise
        chip_boards = []
        icinfo_locked = "HTTP %d" % e.code
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
        "watts_dc": info["watts_dc"], "volts": boards[0].get("voltage_mv") if boards else None, "_boards": boards, "_icinfo_locked": icinfo_locked,
    }


def summarize_chiptemps(readings):
    """The three log columns from (miner_ts, avg, max) tuples: the peak, the median max (the sustained level),
    the median average. None everywhere when there are no readings."""
    if not readings:
        return None, None, None
    maxes = [r[2] for r in readings]
    return max(maxes), float(statistics.median(maxes)), float(statistics.median(r[1] for r in readings))


def migrate_columns(csv_path, columns=None):
    """Bring a log written with an older, shorter header up to COLUMNS in place (`columns` names another
    append-only header: BOARDS_COLUMNS for boards.csv, since 0.9.0).

    Old rows are padded with empty fields so the header and every row agree.
    The original is copied to log.csv.bak first (once; a later migration
    does not overwrite an older backup). Returns a short note for the event
    log when the file changed, saying whether the backup is new or an older
    one was kept, and False otherwise. A missing file, a current header, or
    a header this code does not know are left alone.
    """
    csv_path = Path(csv_path)
    columns = COLUMNS if columns is None else columns
    recovered = _promote_orphan_tmp(csv_path)
    if not csv_path.is_file():
        return recovered
    with open(csv_path, encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n")
        old = header.split(",") if header else []
        if not old or old == columns or columns[:len(old)] != old or len(old) >= len(columns):
            return recovered
        body = f.read()
    pad = "," * (len(columns) - len(old))
    backup = csv_path.with_name(csv_path.name + ".bak")
    if backup.exists():
        note = "the older %s was left as is" % backup.name
    else:
        shutil.copyfile(csv_path, backup)
        note = "copy kept as %s" % backup.name
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(columns) + "\n")
        for line in body.splitlines():
            if line:
                f.write(line + pad + "\n")
    tmp.replace(csv_path)
    return "; ".join(n for n in (recovered, note) if n)


STAMP = "%Y-%m-%d %H:%M:%S"
DEFERRED = "log rotation deferred: "    # the note prefix; a caller tells "could not run" from "did not help"
CARRY_MARGIN_HOURS = 2      # carried beyond keep_hours, so a reader at exactly keep_hours sees whole buckets


def _promote_orphan_tmp(csv_path):
    """Finish a rotation that was interrupted between its two moves.

    `rotate` writes the carried rows to `.tmp`, moves the log to `.1`, then moves `.tmp` into place.
    A crash in that window leaves `.1` and `.tmp` and no log. The `.tmp` is the new log, complete,
    so promote it. Called from `migrate_columns`, which runs once at start.
    """
    working = csv_path.with_name(csv_path.name + ".tmp")
    if csv_path.exists() or not working.is_file():
        return False
    try:        # only something that carries one of our headers; never install a stray file as the log
        with open(working, encoding="utf-8", newline="") as f:
            if not f.readline().startswith("time,"):
                return False
    except OSError:
        return False
    working.replace(csv_path)
    return "recovered %s from %s; a rotation had been interrupted" % (csv_path.name, working.name)


def _rotation_failed(csv_path, working, archive, error):
    """Leave the data directory in a state the next poll can work from, and say what happened."""
    if not csv_path.exists() and working.is_file():
        try:
            working.replace(csv_path)       # the move to .1 went through, so finishing is the safe end
        except OSError:
            pass
    elif working.is_file():
        try:
            working.unlink()
        except OSError:
            pass
    # The exception text carries full paths on Windows ("[WinError 32] ...: C:\Users\<name>\.gbox\log.csv"),
    # and the event log is served to the dashboard, so name the kind of failure and not the path.
    return DEFERRED + "%s (%s: %s); this poll appends as usual and the next one tries again" % (
        csv_path.name, type(error).__name__, getattr(error, "strerror", None) or "no detail")


def rotate(csv_path, keep_hours, now=None):
    """Cap a growing log: carry the last `keep_hours` into a fresh file, archive the whole old one as `.1`.

    No reader changes. `series`, `trials`, `gbox errors` and the dashboard all read the recent end of
    `log.csv`, and the carried rows are exactly what they were already reading, so a rotation is
    invisible on the page. A bare rename would have blanked the 24 h charts, the three-day errors
    chart and the trials table at the moment it fired. The carried rows then exist twice on disk, here
    and in `.1`, but nothing reads `.1`, so nothing double-counts them. History older than the cap is
    archived, not shown: that was the deliberate trade against readers that span two files.

    `CARRY_MARGIN_HOURS` more than `keep_hours` is carried. Carrying exactly `keep_hours` leaves the
    oldest bucket of a reader asking for exactly that window half full: measured on a copy of the
    real 8.5 MB log, one of 145 half-hour buckets changed across a rotation, the oldest, 29 samples
    where it had been 60. The three-day errors chart asks for exactly 72 h, so the margin is what
    keeps a rotation invisible on the page.

    A row whose first field is not a timestamp is dropped from the carry and kept in the archive.

    Any OSError leaves every file as it was and returns a note saying so. On Windows a reader holding
    the log open makes the move a PermissionError, and the honest answer is to append this poll's
    sample as usual and try again on the next one. Returns a note for the event log, or False when
    there was nothing to do.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return False
    before = csv_path.stat().st_size
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(hours=keep_hours + CARRY_MARGIN_HOURS)
    working = csv_path.with_name(csv_path.name + ".tmp")
    archive = csv_path.with_name(csv_path.name + ".1")
    kept = 0
    try:
        with open(csv_path, encoding="utf-8", newline="") as src:
            header = src.readline().strip()
            if not header:
                return False
            with open(working, "w", encoding="utf-8", newline="") as dst:
                dst.write(header + "\n")
                for line in src:
                    try:
                        when = datetime.datetime.strptime(line.split(",", 1)[0].strip(), STAMP)
                    except ValueError:
                        continue
                    if when >= cutoff:
                        dst.write(line.strip() + "\n")
                        kept += 1
        csv_path.replace(archive)
        working.replace(csv_path)
    except OSError as e:
        return _rotation_failed(csv_path, working, archive, e)
    return ("rotated %s: %d bytes to %d, %d row%s carried (the last %d h plus a %d h margin), old file kept as %s"
            % (csv_path.name, before, csv_path.stat().st_size, kept, "" if kept == 1 else "s",
               keep_hours, CARRY_MARGIN_HOURS, archive.name))


def error_row(exc):
    return {"http": "ERR:%s:%s" % (type(exc).__name__, str(exc)[:60].replace(",", ";").replace("\n", " "))}


class Poller(threading.Thread):
    def __init__(self, miner, csv_path, interval, watchdog=None, events=None, clock=time.time, plug=None, scheduler=None,
                 syslog_interval=0, board_source="auto", devs4028_port=4028, max_bytes=0, keep_hours=72):
        super().__init__(name="gbox-poller", daemon=True)
        self.miner = miner
        self.syslog_interval = float(syslog_interval or 0)   # 0: never read the cgminer log
        self.syslog_errors = 0
        self._syslog_next = None    # clock time of the next log read; None means at the next good sample
        self._syslog_cursor = None  # api.LogCursor: where the last read ended, a place in the log (0.10.0)
        self.scheduler = scheduler  # gbox.power.Scheduler: ticked once per sample, after the watchdog
        self.csv_path = Path(csv_path)
        self.boards_csv_path = self.csv_path.with_name("boards.csv")
        # 0.9.0: minerlog.csv, the non-routine lines of each log read as labels and counts. Its own cursor, so a
        # classifier failure re-reads its lines next time without costing the temperatures theirs.
        self.minerlog_path = self.csv_path.with_name("minerlog.csv")
        self.minerlog_max_bytes = MINERLOG_MAX_BYTES
        self._minerlog_cursor = None
        self._minerlog_full = False
        # 0.8.0: the cap that fires a rotation, in bytes (config.log.max_mb), and the window it carries.
        # 0 is no rotation, which is what every test that does not care about it gets.
        self.max_bytes = int(max_bytes or 0)
        self.keep_hours = int(keep_hours)
        self._rotation_off = set()   # paths whose carry does not fit under the cap; see _rotate_if_full
        self.interval = float(interval)
        self.watchdog = watchdog
        if watchdog is not None:
            watchdog.log_reader = self.syslog_interval > 0      # 0.10.0 B needs the log; without it B is off
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
        self._icinfo_seen = False     # True once icinfo has answered in this run; from then its failure is an error

    def _sample(self):
        """One sample via the transport `board_source` picks. "auto": the very first sample probes port
        4028 directly; a `MinerError` there (never `NoCredentials` -- port 4028 needs no token, so a
        missing one says nothing about the socket) falls back to `/dbg/minerinfo` for the rest of the
        run, with one event line. Once resolved, by success or by fallback, the source never changes
        again for this poller. The fallback is only decided once `/dbg/minerinfo` answers (0.9.1): a
        miner still booting has neither up yet, and that says nothing about port 4028."""
        if self._source is not None:
            return sample(self.miner, self._source, devs4028_port=self.devs4028_port,
                          icinfo_optional=not self._icinfo_seen)
        try:
            self.miner.devs4028(port=self.devs4028_port)   # probe; sample() below reads it again, once
        except api.MinerError:
            row = sample(self.miner, "minerinfo", icinfo_optional=not self._icinfo_seen)   # raises: undecided
            self._source = "minerinfo"
            if self.events:
                self.events.write("miner: port 4028 closed or silent; reading boards from /dbg/minerinfo")
            return row
        self._source = "4028"
        return sample(self.miner, "4028", devs4028_port=self.devs4028_port, icinfo_optional=not self._icinfo_seen)

    def _append_boards(self, now, boards):
        """boards.csv beside log.csv: one row per board per poll, header on create. Only called when the
        unit has more than one board; a single-board unit never gets this file."""
        self._rotate_if_full(self.boards_csv_path)
        new = not self.boards_csv_path.exists() or self.boards_csv_path.stat().st_size == 0
        with open(self.boards_csv_path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(",".join(BOARDS_COLUMNS) + "\n")
            for b in boards:
                row = {"time": now, "board": b["board"], "elapsed": b["elapsed"], "mhs_20s": b["mhs_20s"],
                       "mhs_av": b["mhs_av"], "accepted": b["accepted"], "rejected": b["rejected"],
                       "hwerr": b["hw_errors"], "hwerr_pct": b["hw_pct"], "clock": b["clock"],
                       "tstemp0": b["chip_temp"], "tstemp2": b["board_temp"], "rebootcnt": b["rebootcnt"],
                       "overheat": b["overheat"], "volts": b.get("voltage_mv")}
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

    def read_chiptemps(self, stamp=None):
        """The hottest-chip columns for this row: (peak, level, chip_avg) from one log read when one is due,
        else (None, None, None). A failed read is a blank and a count, never an error row. The same read
        feeds minerlog.csv (0.9.0); `stamp` is the row's own time for those lines."""
        now = self._clock()
        wanted = bool(getattr(self.watchdog, "log_wanted", False))
        if self.syslog_interval <= 0 or (self._syslog_next is not None and now < self._syslog_next and not wanted):
            return None, None, None
        self._syslog_next = now + self.syslog_interval
        try:
            text = self.miner.syslog()
        except Exception:
            self.syslog_errors += 1
            self._tell_watchdog_log(False, [])
            return None, None, None
        signals = []
        try:
            self._append_minerlog(stamp or datetime.datetime.now().strftime(STAMP), text, signals)
        except Exception:                   # evidence is never worth a temperature reading, let alone a sample
            self.syslog_errors += 1
        self._tell_watchdog_log(True, signals)
        try:
            readings, cursor = api.parse_chiptemps(text, cursor=self._syslog_cursor, first_minutes=FIRST_READ_MINUTES)
        except Exception:
            self.syslog_errors += 1
            return None, None, None
        self._syslog_cursor = cursor
        if not readings:
            return None, None, None
        return summarize_chiptemps(readings)

    def _tell_watchdog_log(self, ok, signals):
        """Hand a log read's outcome to the watchdog (0.10.0 B), if it takes one: a stand-in may not."""
        observe_log = getattr(self.watchdog, "observe_log", None)
        if observe_log is not None:
            observe_log(ok, signals)

    def _board_absent(self, row):
        """The controller answered with no hashboard behind it (0.9.0): clock 0 and the board sensor at its
        no-sensor value, from a mining process that has been up long enough to have found its board. Only on a
        model whose profile vouches for the signature; the model is known from the first good sample on. A reset
        burst, the other thing a sick board does, keeps a real clock and real temperatures and is not this."""
        model = (self.miner_status or {}).get("model")
        if not model or not models.profile_for(model).get("absent_signature"):
            return False
        clock, temp, elapsed = row.get("clock"), row.get("tstemp2"), row.get("elapsed")
        return (clock == 0 and temp is not None and temp <= ABSENT_SENSOR_BELOW
                and elapsed is not None and elapsed >= ABSENT_MIN_ELAPSED)

    def _append_minerlog(self, stamp, text, signals=None):
        """One row per label for the lines of this log read that are newer than the last one's. A first read
        looks back FIRST_READ_MINUTES by the miner's clock, as the temperatures do. A quiet read writes nothing.
        `signals` receives the read's pool evidence for the watchdog (0.10.0)."""
        rows, self._minerlog_cursor = api.classify_syslog(text, cursor=self._minerlog_cursor,
                                                          first_minutes=FIRST_READ_MINUTES, signals=signals)
        if not rows:
            return
        path = self.minerlog_path
        size = path.stat().st_size if path.exists() else 0
        if self.minerlog_max_bytes and size >= self.minerlog_max_bytes:
            # checked on every read, so a file moved aside starts a new one; the flag only keeps the event to one line
            if not self._minerlog_full and self.events:
                self.events.write("service: minerlog.csv reached its %d MB cap and is no longer written; move it "
                                  "aside to start a new one" % round(self.minerlog_max_bytes / 1024 / 1024))
            self._minerlog_full = True
            return
        self._minerlog_full = False
        with open(path, "a", encoding="utf-8", newline="") as f:
            if size == 0:
                f.write(",".join(MINERLOG_COLUMNS) + "\n")
            for label, count, first, last in rows:
                f.write("%s,%s,%d,%s,%s\n" % (stamp, label, count, first, last))

    def stop(self):
        self._stop.set()

    def poll_once(self):
        """Take one sample, append it, feed the watchdog. Returns the row."""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            row = self._sample()
            self.samples += 1
            self.last_error = None
            if not row.get("_icinfo_locked"):
                self._icinfo_seen = True
            elif not self._icinfo_locked:
                self._icinfo_locked = True
                why = "401 persisted" if row["_icinfo_locked"] is True else "answered %s" % row["_icinfo_locked"]
                if self.events:
                    self.events.write("miner: /dbg/icinfo %s; chip-level columns blank until it answers" % why)
            if self.miner_status is None:            # one extra request, once, after the sample (never concurrent)
                try:
                    self.miner_status = self.miner.status()
                except Exception:
                    pass
            row["hot_peak"], row["hot_level"], row["chip_avg"] = self.read_chiptemps(now)   # a fourth request, when due
        except api.NoCredentials:
            row = {"http": "ERR:NoCredentials:waiting for a token from the dashboard"}
            self.errors += 1
            self.last_error = row["http"]
        except Exception as e:
            row = error_row(e)
            self.errors += 1
            self.last_error = row["http"]
            # 0.10.0 B: a failed sample can still leave the web backend answering (port 4028 dies about 8 min
            # into a pool outage). The watchdog asks for the log when a verdict waits on it, and while an upstream
            # episode runs it reads on the usual schedule, so a backend that dies too is noticed. Sequential.
            wd = self.watchdog
            if getattr(wd, "log_wanted", False) or getattr(wd, "upstream_since", None) is not None:
                self.read_chiptemps(now)
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
            self.watchdog.observe(ok, row.get("accepted"), self._clock(), hashing=ok and (row.get("mhs_20s") or 0) > 0,
                                  absent=ok and self._board_absent(row))
            self.watchdog.check()
        if self.scheduler is not None:
            try:
                self.scheduler.tick()
            except Exception as e:          # a schedule bug must never cost a sample
                if self.events:
                    self.events.write("service: schedule tick failed: %s" % e)
        return row

    def _rotate_if_full(self, path):
        """Rotate `path` when it has reached the cap, with one event line saying what moved where.

        Checked before each write, so a file passes the cap by at most one poll, and it is also where a
        rotation interrupted by a crash is finished: `log.csv` gets that at start from `migrate_columns`,
        but `boards.csv` has no equivalent, and a stranded `.tmp` holds the only copy of the carried rows.
        A carry larger than the cap would rotate on every poll and replace the archive each time, so
        that case turns rotation of this file off with one line saying which setting to change.

        A rotation that cannot
        run writes its own line and leaves every file as it was; either way the caller then appends this
        poll's rows, because a sample is never worth losing to housekeeping.
        """
        if not self.max_bytes or path in self._rotation_off:
            return
        recovered = _promote_orphan_tmp(path)      # a rotation of THIS file interrupted by a crash
        if recovered and self.events:
            self.events.write("service: " + recovered)
        if not path.is_file() or path.stat().st_size < self.max_bytes:
            return
        note = rotate(path, self.keep_hours)
        if note and self.events:
            self.events.write("service: " + note)
        if note and not str(note).startswith(DEFERRED) and path.is_file() and path.stat().st_size >= self.max_bytes:
            # The carry is bigger than the cap, so the fresh file is born over it and the next poll would
            # rotate again, and every poll after that -- each one replacing .1 with what it just carried,
            # so the history the first rotation archived would live for one poll. Stop, and say what to
            # change. Nothing is lost by stopping: the file simply keeps growing, as it did before 0.8.0.
            self._rotation_off.add(path)
            if self.events:
                self.events.write(
                    "service: %s is still %.1f MB after carrying %d h, which is at or over the %.1f MB cap, "
                    "so rotating again would only overwrite %s.1 with the same rows. Rotation of this file "
                    "is off until the service restarts: raise log.max_mb or cut log.keep_hours."
                    % (path.name, path.stat().st_size / (1024.0 * 1024), self.keep_hours,
                       self.max_bytes / (1024.0 * 1024), path.name))

    def _append(self, row):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_full(self.csv_path)
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
