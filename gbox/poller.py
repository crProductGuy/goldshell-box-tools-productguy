"""The poll loop: one sample per interval into log.csv, then the watchdog.

One thread, two requests per sample (minerinfo, icinfo), through the
serialized session. The columns are what the dashboard's fan and
temperature chart reads by header name, so adding a column is safe and
renaming one is not.
"""
import datetime
import threading
import time
from pathlib import Path

from . import api

COLUMNS = ["time", "http", "elapsed", "mhs_av", "mhs_20s", "hwerr", "hwerr_pct", "accepted",
           "rejected", "clock", "fan0", "fan1", "tstemp0", "tstemp1", "tstemp2", "rebootcnt",
           "weak_chips"]


def sample(miner):
    """One sample as a dict keyed by COLUMNS. Raises MinerError on failure."""
    info = miner.minerinfo()
    boards = miner.boards()
    weak = ";".join("%d:%d/%d" % (c["chip"], c["good"], c["bad"])
                    for board in boards for c in api.weak_chips(board))
    return {
        "http": "ok", "elapsed": info["elapsed"], "mhs_av": info["mhs_av"], "mhs_20s": info["mhs_20s"],
        "hwerr": info["hw_errors"], "hwerr_pct": info["hw_pct"], "accepted": info["accepted"],
        "rejected": info["rejected"], "clock": info["clock"], "fan0": info["fan0"], "fan1": info["fan1"],
        "tstemp0": info["chip_temp"], "tstemp1": info["chip_temp1"], "tstemp2": info["board_temp"],
        "rebootcnt": info["rebootcnt"], "weak_chips": weak,
    }


def error_row(exc):
    return {"http": "ERR:%s:%s" % (type(exc).__name__, str(exc)[:60].replace(",", ";").replace("\n", " "))}


class Poller(threading.Thread):
    def __init__(self, miner, csv_path, interval, watchdog=None, events=None, clock=time.time):
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
