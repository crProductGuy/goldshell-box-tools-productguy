"""The event log: one timestamped line per thing the tools did or noticed.

Restarts sent, settings changed, watchdog decisions. Never a URL, never a
credential. Served to the dashboard as /api/events.
"""
import datetime
import threading
from collections import deque
from pathlib import Path


# 0.8.0: the event log is capped too. Lines rather than hours, because the watchdog seeds its daily
# caps from the last KEEP_LINES at start, and that is what has to survive a rotation for a restarted
# service to know what it already did today. The whole old file becomes events.log.1, which nothing reads.
MAX_BYTES = 5 * 1024 * 1024
KEEP_LINES = 4000


class EventLog:
    def __init__(self, path=None, max_bytes=MAX_BYTES, keep_lines=KEEP_LINES):
        self.path = Path(path) if path else None
        self.max_bytes = int(max_bytes or 0)        # 0: no rotation
        self.keep_lines = int(keep_lines)
        self._lock = threading.Lock()

    def _rotate(self):
        """At the cap, carry the last `keep_lines` into a fresh file and archive the whole old one as .1.

        Called from `write` with the lock held, and it returns its note instead of writing one, so the
        note lands in the new file without a second trip through the lock. Any OSError (a reader holding
        the file open on Windows) leaves every file as it was, and the line still gets written.
        """
        if not self.max_bytes or not self.path:
            return None
        working = self.path.with_name(self.path.name + ".tmp")
        if not self.path.exists() and working.is_file():
            # a rotation interrupted between its two moves: the .tmp is the new log, complete
            try:
                working.replace(self.path)
                return "service: recovered %s from %s; a rotation had been interrupted" % (self.path.name, working.name)
            except OSError:
                return None
        if not self.path.is_file():
            return None
        before = self.path.stat().st_size
        if before < self.max_bytes:
            return None
        archive = self.path.with_name(self.path.name + ".1")
        try:
            with open(self.path, encoding="utf-8", errors="replace") as src:
                kept = list(deque(src, maxlen=self.keep_lines))
            with open(working, "w", encoding="utf-8") as dst:
                dst.writelines(kept)
            self.path.replace(archive)
            working.replace(self.path)
        except OSError as e:
            if not self.path.exists() and working.is_file():
                try:
                    working.replace(self.path)      # the move to .1 went through, so finishing is the safe end
                except OSError:
                    pass
            elif working.is_file():
                try:
                    working.unlink()
                except OSError:
                    pass
            # Not str(e): the exception text carries the full path on Windows, and this file is served
            # to the dashboard. The kind of failure is what a reader needs.
            return ("service: event-log rotation deferred: %s (%s: %s); the next line tries again"
                    % (self.path.name, type(e).__name__, getattr(e, "strerror", None) or "no detail"))
        after = self.path.stat().st_size
        note = ("service: rotated %s: %d bytes to %d, the last %d lines carried, old file kept as %s"
                % (self.path.name, before, after, len(kept), archive.name))
        if after >= self.max_bytes:
            # The same trap the poller guards: if the carried lines do not fit under the cap, every
            # further line would rotate again and replace .1 with what it just carried.
            self.max_bytes = 0
            note += (". It is still at the cap after carrying %d lines, so rotation is off until the "
                     "service restarts." % len(kept))
        return note

    def write(self, message):
        line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        with self._lock:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                note = self._rotate()
                with open(self.path, "a", encoding="utf-8") as f:
                    if note:
                        f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note))
                    f.write(line + "\n")
        return line

    def tail(self, n=200):
        if not self.path or not self.path.exists():
            return []
        with self._lock, open(self.path, encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=n))
