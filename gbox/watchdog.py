"""Stall detection and capped soft restarts.

Pure logic, fed one sample per poll by the poller and judged with an
injectable clock so the rules can be tested in milliseconds.

Rules, all in wall-clock terms and converted to sample counts from the poll
interval:

- unreachable: every sample in the last `unreachable_minutes` failed
- stalled: every sample in the last `stall_minutes` succeeded and the
  accepted-share counter never moved
- a restart is followed by `min_gap_minutes` of silence before judging again
- at most `max_restarts_per_day` restarts in any rolling 24 hours; past the
  cap the reason is logged once per episode and nothing is sent
- samples with a gap in them (the PC slept, the service paused) are not
  judged until a full window of fresh, contiguous samples exists
"""
import time
from collections import deque


class Watchdog:
    def __init__(self, restart, events, interval, stall_minutes=5, unreachable_minutes=2,
                 min_gap_minutes=10, max_restarts_per_day=6, clock=time.time):
        self._restart = restart
        self._events = events
        self.interval = float(interval)
        self.stall_rows = max(2, round(stall_minutes * 60 / self.interval))
        self.err_rows = max(1, round(unreachable_minutes * 60 / self.interval))
        self.min_gap = min_gap_minutes * 60
        self.max_restarts = max_restarts_per_day
        self._clock = clock
        self._rows = deque(maxlen=self.stall_rows * 2)
        self._restart_times = deque()
        self._capped_logged = False
        self.last_restart = None
        self.last_reason = None

    def observe(self, ok, accepted, t=None):
        self._rows.append((self._clock() if t is None else t, bool(ok), accepted))

    def restarts_today(self):
        cutoff = self._clock() - 86400
        while self._restart_times and self._restart_times[0] < cutoff:
            self._restart_times.popleft()
        return len(self._restart_times)

    def external_restart(self):
        """Someone else (the dashboard's button) restarted the miner: start the settle gap.

        Not counted against the daily cap, which exists to stop the watchdog
        itself from looping.
        """
        self.last_restart = self._clock()

    def diagnose(self):
        """The reason a restart is due, or None."""
        now = self._clock()
        floor = (self.last_restart + self.min_gap) if self.last_restart else float("-inf")
        rows = [r for r in self._rows if r[0] > floor]
        if len(rows) < self.stall_rows:
            return None
        recent = rows[-self.stall_rows:]
        span = recent[-1][0] - recent[0][0]
        contiguous = span <= (self.stall_rows - 1) * self.interval * 1.5
        fresh = now - recent[-1][0] <= 2 * self.interval
        if not (contiguous and fresh):
            return None
        if all(not r[1] for r in recent[-self.err_rows:]):
            return "miner unreachable for %d min" % round(self.err_rows * self.interval / 60)
        if all(r[1] for r in recent) and len({r[2] for r in recent}) == 1 and recent[0][2] is not None:
            return "accepted shares frozen for %d min" % round(self.stall_rows * self.interval / 60)
        return None

    def check(self):
        """Judge the samples seen so far; send a restart if one is due. Returns the reason or None."""
        reason = self.diagnose()
        self.last_reason = reason
        if not reason:
            self._capped_logged = False
            return None
        if self.restarts_today() >= self.max_restarts:
            if not self._capped_logged:
                self._events.write("watchdog: %s, but %d restarts in 24 h is the cap; not restarting"
                                   % (reason, self.max_restarts))
                self._capped_logged = True
            return reason
        now = self._clock()
        self.last_restart = now
        self._restart_times.append(now)
        try:
            self._restart()
            self._events.write("watchdog: restart #%d sent (%s)" % (self.restarts_today(), reason))
        except Exception as e:
            self._events.write("watchdog: restart attempt failed: %s (%s)" % (e, reason))
        return reason
