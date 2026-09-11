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
"""
import datetime
import shutil
import threading
import time
from pathlib import Path

from . import api

COLUMNS = ["time", "http", "elapsed", "mhs_av", "mhs_20s", "hwerr", "hwerr_pct", "accepted",
           "rejected", "clock", "fan0", "fan1", "tstemp0", "tstemp1", "tstemp2", "rebootcnt",
           "weak_chips", "nonces_good", "nonces_bad", "temp_target", "overheat", "watts"]


def sample(miner):
    """One sample as a dict keyed by COLUMNS. Raises MinerError on failure."""
    info = miner.minerinfo()
    boards = miner.boards()
    setting = miner.setting()
    weak = ";".join("%d:%d/%d" % (c["chip"], c["good"], c["bad"])
                    for board in boards for c in api.weak_chips(board))
    chips = [c for board in boards for c in board]
    return {
        "http": "ok", "elapsed": info["elapsed"], "mhs_av": info["mhs_av"], "mhs_20s": info["mhs_20s"],
        "hwerr": info["hw_errors"], "hwerr_pct": info["hw_pct"], "accepted": info["accepted"],
        "rejected": info["rejected"], "clock": info["clock"], "fan0": info["fan0"], "fan1": info["fan1"],
        "tstemp0": info["chip_temp"], "tstemp1": info["chip_temp1"], "tstemp2": info["board_temp"],
        "rebootcnt": info["rebootcnt"], "weak_chips": weak,
        "nonces_good": sum(c["good"] for c in chips), "nonces_bad": sum(c["bad"] for c in chips),
        "temp_target": setting.get("temp_target"), "overheat": info["overheat"],
    }


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
    def __init__(self, miner, csv_path, interval, watchdog=None, events=None, clock=time.time, plug=None):
        super().__init__(name="gbox-poller", daemon=True)
        self.miner = miner
        self.csv_path = Path(csv_path)
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

    def stop(self):
        self._stop.set()

    def poll_once(self):
        """Take one sample, append it, feed the watchdog. Returns the row."""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            row = sample(self.miner)
            self.samples += 1
            self.last_error = None
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
        self.latest = row
        if self.watchdog is not None and not row["http"].startswith("ERR:NoCredentials"):
            self.watchdog.observe(row["http"] == "ok", row.get("accepted"), self._clock())
            self.watchdog.check()
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
