"""The event log: one timestamped line per thing the tools did or noticed.

Restarts sent, settings changed, watchdog decisions. Never a URL, never a
credential. Served to the dashboard as /api/events.
"""
import datetime
import threading
from collections import deque
from pathlib import Path


class EventLog:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, message):
        line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        with self._lock:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        return line

    def tail(self, n=200):
        if not self.path or not self.path.exists():
            return []
        with self._lock, open(self.path, encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=n))
