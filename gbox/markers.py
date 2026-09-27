"""Event markers for things the miner does on its own (0.10.2): a restart, and fans running high.

The watchdog writes what gbox did to the miner. These two lines are about what the miner did by itself,
which until now showed only in log.csv: on 2026-09-26/27 the SC-BOX rebooted three times in under a
minute each, faster than the watchdog's two-minute unreachable rule, and left no line anywhere. The fan
surge the owner noticed at 09:12 on 09-27 was the firmware's boot ramp after one of those reboots.

Both rules are device-independent on purpose, so they work on a model nobody here has read:

- **Restart.** Every cgminer-based firmware reports the mining process's uptime (`elapsed`). Its start
  time is `now - elapsed`; when that jumps forward, the process started again. Comparing start times,
  not uptimes, also catches a restart hidden behind a long outage, where the new uptime can already be
  larger than the old one. Between two back-to-back polls with no failure between them, the uptime must
  also have fallen: a forward step of this PC's clock (a time sync after hibernation) moves the implied
  start without anything restarting. The cause is read from the event log's own lines written in
  between (the watchdog, the plug, the dashboard); none there means the miner did it on its own.
- **Fans high.** Judged against the unit's own settled fan speed in the current run, not a rated
  maximum, because most models have none on record. A fan is high at `HIGH_RATIO` times its settled
  median and at least `HIGH_DELTA_RPM` above it, on `HIGH_SAMPLES` polls in a row. The first
  `SETTLE_MINUTES` of every run are left out, because firmware starts its fans near full speed and ramps
  them down as the board warms (4,300 RPM falling to 1,260 over 15 minutes on the SC-BOX). A settings
  change from the page (a clock, a fan target, a trial step) does the same ramp WITHOUT resetting the
  uptime: all six surges a replay of the SC-BOX's log from 2026-09-05 flagged came within a minute of a
  `dashboard:` line. So a run, for this rule, starts at a restart or at such a line, and each run gets a
  new baseline, because a clock change also moves the settled speed. Where the model table does have a
  maximum, `NEAR_MAX` of it also counts, but only for a fan that has also risen by `HIGH_DELTA_RPM`: a
  model that settles near its maximum is not "high" for doing so. An episode that outlasts
  `EPISODE_HOURS` is closed as a new level and the baseline starts again from it, so a hot room cannot
  hold one open for ever; after an episode ends, a new one waits `REARM_MINUTES`, so a fan that hunts
  around the threshold writes a pair of lines, not one every few minutes.

Neither rule acts on anything. They write lines; the watchdog is unchanged.
"""
import datetime
import re
import statistics
from collections import deque


RESTART_SLACK_SECONDS = 90      # start-time jitter tolerated: poll timing, whole-second uptimes, a clock nudge
SETTLE_MINUTES = 30             # mining uptime before a fan reading counts toward the baseline or is judged
BASELINE_MINUTES = 30           # settled samples needed, by time span, before anything is judged
BASELINE_HOURS = 6              # the baseline window: recent enough to follow the room through a day
HIGH_RATIO = 1.5
HIGH_DELTA_RPM = 600
HIGH_SAMPLES = 2                # polls in a row over the line before an episode opens: one odd reading is not one
NORMAL_RATIO = 1.25             # back to normal: every fan under this multiple of its baseline ...
NORMAL_SAMPLES = 3              # ... this many samples in a row
REARM_MINUTES = 30              # after an episode ends, the next one may not open for this long
EPISODE_HOURS = 2               # an episode this long is a new level, not an alarm: closed, and the baseline restarts
NEAR_MAX = 0.9                  # of the model's rated fan maximum, where the table has one
FANS = ("fan0", "fan1")         # the two fan columns log.csv carries

_STAMPED = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$")
# What can restart the mining process from outside, strongest explanation first. The first match is quoted.
# A soft restart whose request failed ("restart attempt failed") ranks last: a timed-out request may still have
# reached the miner, so it is quoted rather than the restart called the miner's own (six such on the SC-BOX).
CAUSES = (re.compile(r"^power: (?:cycled|switched)"), re.compile(r"^watchdog: restart #"),
          re.compile(r"^dashboard: "), re.compile(r"^hold: started by the schedule"),
          re.compile(r"^watchdog: restart attempt failed"))
# Lines that start a new fan run without resetting the uptime: settings pushed from the page, power switched on.
NEW_RUN = re.compile(r"^(?:dashboard: |power: switched on)")
UNSAMPLED_SECONDS = 180         # a gap this long between good samples with no failed one: gbox was not sampling
CAUSE_CHARS = 100
CAUSE_TAIL_LINES = 400          # a long outage writes watchdog lines every few minutes; reach past them


def duration(seconds):
    """'13 h 4 min', '7 min', '45 s'."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return "%d s" % s
    h, m = divmod(s // 60, 60)
    return ("%d h %d min" % (h, m)) if h else ("%d min" % m)


def _rpm(v):
    return "%d" % round(v)


def _stamp(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


class Markers:
    def __init__(self, events):
        self._events = events
        self._start = None              # this run's start, as `t - elapsed`
        self._last_ok = None            # (t, elapsed) of the last good sample
        self._down_since = None         # first failed sample since the last good one
        self._base = deque()            # (t, {fan: rpm}) settled samples of this run, oldest first
        self._high = None               # running episode: {since, peak, base, calm}
        self._over = 0                  # polls in a row over the line, before an episode opens
        self._quiet_until = None        # no new episode before this clock time (REARM_MINUTES)
        self._run_from = None           # latest NEW_RUN line seen, as clock time: the fan run restarts there
        self._seen_recent = 0           # the event log's `written` count at the last look

    # --- restart -------------------------------------------------------------------------------------

    def _cause(self, since, now):
        """What the event log says happened between `since` and `now`, or None."""
        tail = getattr(self._events, "tail", None)
        lines = tail(CAUSE_TAIL_LINES) if tail else []
        found = []
        for line in lines:
            m = _STAMPED.match((line or "").rstrip("\n"))
            t = _stamp(m.group(1)) if m else None
            if t is not None and since - 5 <= t <= now + 5:
                found.append(m.group(2))
        for pattern in CAUSES:
            for text in found:
                if pattern.match(text):
                    return text if len(text) <= CAUSE_CHARS else text[:CAUSE_CHARS - 3] + "..."
        return None

    def _is_restart(self, t, elapsed, start):
        last_t, last_elapsed = self._last_ok
        if elapsed + RESTART_SLACK_SECONDS < last_elapsed:
            return True                 # the uptime fell: nothing else does that (catches a boot loop's short runs)
        if start <= self._start + RESTART_SLACK_SECONDS:
            return False
        # The start moved forward. Across an outage or a gap in sampling that is a restart. Between two
        # back-to-back good polls it is one only if the uptime fell too; otherwise this PC's clock stepped.
        return self._down_since is not None or t - last_t > UNSAMPLED_SECONDS or elapsed < last_elapsed

    def _restarted(self, t, elapsed):
        last_t, last_elapsed = self._last_ok
        cause = self._cause(last_t, t)
        if self._down_since is not None:
            seen = "unreachable for %s" % duration(t - self._down_since)
        elif t - last_t > UNSAMPLED_SECONDS:
            seen = "gbox was not sampling for %s, so the moment is not known" % duration(t - last_t)
        else:
            seen = "never seen unreachable (back within one poll)"
        self._events.write("miner: restarted %s; mining uptime had been %s, now %s; %s"
                           % ("after [%s]" % cause if cause else "on its own", duration(last_elapsed),
                              duration(elapsed), seen))

    # --- fans ----------------------------------------------------------------------------------------

    def _fans(self, row):
        out = {}
        for k in FANS:
            v = row.get(k)
            if isinstance(v, (int, float)) and v > 0:
                out[k] = float(v)
        return out

    def _baseline(self):
        """{fan: settled median} once the settled samples span BASELINE_MINUTES, else None."""
        if not self._base or self._base[-1][0] - self._base[0][0] < BASELINE_MINUTES * 60:
            return None
        out = {}
        for k in FANS:
            vals = [f[k] for _, f in self._base if k in f]
            if vals:
                out[k] = statistics.median(vals)
        return out or None

    def _is_high(self, fans, base, fan_max):
        for k, v in fans.items():
            b = base.get(k)
            if not b or v < b + HIGH_DELTA_RPM:
                continue
            if v >= b * HIGH_RATIO or (fan_max and v >= fan_max * NEAR_MAX):
                return True
        return False

    def _is_normal(self, fans, base, fan_max):
        for k, v in fans.items():
            b = base.get(k)
            if b and v >= b * NORMAL_RATIO:
                return False
            if b and fan_max and v >= fan_max * NEAR_MAX and v >= b + HIGH_DELTA_RPM:
                return False
        return True

    def _end_high(self, t, why=""):
        h = self._high
        self._high = None
        self._over = 0
        self._quiet_until = t + REARM_MINUTES * 60
        self._events.write("fans: back to normal after %s%s (peak %s RPM)"
                           % (duration(t - h["since"]), why, _rpm(h["peak"])))

    def _new_runs(self):
        """Clock time of the newest NEW_RUN line this process wrote since the last look, or None. Reads the
        event log's in-memory lines, never the file: this runs every poll."""
        since = getattr(self._events, "recent_since", None)
        if since is None:
            return None
        fresh, self._seen_recent = since(self._seen_recent)
        newest = None
        for line in fresh:
            m = _STAMPED.match(line)
            if m and NEW_RUN.match(m.group(2)):
                newest = _stamp(m.group(1)) or newest
        return newest

    def _new_run(self, t, why):
        self._base.clear()
        self._over = 0
        if self._high is not None:
            self._end_high(t, why)

    def _judge_fans(self, row, t, elapsed, fan_max):
        fans = self._fans(row)
        run_from = self._new_runs()
        if run_from is not None:
            self._run_from = run_from
            self._new_run(t, ", ended by a settings change")
        settled = elapsed >= SETTLE_MINUTES * 60 and (self._run_from is None or t - self._run_from >= SETTLE_MINUTES * 60)
        if not fans or not settled:
            return
        if self._high is not None:
            h = self._high
            h["peak"] = max([h["peak"]] + list(fans.values()))
            h["calm"] = h["calm"] + 1 if self._is_normal(fans, h["base"], fan_max) else 0
            if h["calm"] >= NORMAL_SAMPLES:
                self._end_high(t)
            elif t - h["since"] >= EPISODE_HOURS * 3600:
                self._end_high(t, "; this is the new settled level, so the baseline starts again from here")
                self._base.clear()
            return
        base = self._baseline()
        armed = self._quiet_until is None or t >= self._quiet_until
        if base is not None and armed and self._is_high(fans, base, fan_max):
            self._over += 1
            if self._over >= HIGH_SAMPLES:
                self._high = {"since": t, "peak": max(fans.values()), "base": base, "calm": 0}
                now = " / ".join(_rpm(fans[k]) for k in FANS if k in fans)
                settled = " / ".join(_rpm(base[k]) for k in FANS if k in base)
                temp = row.get("tstemp0")
                self._events.write("fans: high, %s RPM against %s RPM settled in this run%s"
                                   % (now, settled, ("; board %g C" % temp) if isinstance(temp, (int, float)) and temp > 0 else ""))
            return                      # a reading over the line never enters the baseline
        self._over = 0
        self._base.append((t, fans))
        while self._base and self._base[0][0] < t - BASELINE_HOURS * 3600:
            self._base.popleft()

    # --- the one entry point -------------------------------------------------------------------------

    def observe(self, row, t, fan_max=None):
        """One poll's row (as written to log.csv) sampled at clock time `t`. `fan_max`: the model's rated fan
        maximum in RPM, or None. Writes at most a few event lines; never raises on a malformed row."""
        http = row.get("http") or ""
        if http != "ok":
            # waiting for a token says nothing about the miner being reachable
            if http.startswith("ERR:") and not http.startswith("ERR:NoCredentials") and self._down_since is None:
                self._down_since = t
            return
        elapsed = row.get("elapsed")
        if not isinstance(elapsed, (int, float)):
            self._down_since = None     # it answered; only the uptime is missing
            return
        start = t - elapsed
        if self._start is not None and self._last_ok is not None and self._is_restart(t, elapsed, start):
            self._restarted(t, elapsed)
            self._new_run(t, ", ended by the restart")
        self._start = start
        self._last_ok = (t, elapsed)
        self._down_since = None
        self._judge_fans(row, t, elapsed, fan_max)
