"""Stall detection, capped soft restarts, and the power rung above them.

Pure logic, fed one sample per poll by the poller and judged with an
injectable clock so the rules can be tested in milliseconds.

Rules, all in wall-clock terms and converted to sample counts from the poll
interval:

- unreachable: every sample in the last `unreachable_minutes` failed
- stalled: every sample in the last `stall_minutes` succeeded and the
  accepted-share counter never moved
- a restart is followed by `min_gap_minutes` of silence before judging again;
  the unreachable rule then reads its own window, which may reach back into
  the gap, so a controller still dark at the gap's end gets the next rung on
  the next sample; the stall rule needs a full window of samples after the gap
- at most `max_restarts_per_day` restarts in any rolling 24 hours; past the
  cap the reason is logged once per episode and nothing is sent
- samples with a gap in them (the PC slept, the service paused) are not
  judged until a window of fresh, contiguous samples exists

The power rung, only when a plug is configured. A frozen controller cannot
take a soft restart (three episodes on record: no HTTP, no ping, the PUT
times out, the hashboard idle at about 34 W). The rung reads exactly that
signature and nothing looser:

1. the reason is "unreachable" (a stalled-but-reachable miner gets soft
   restarts, as before)
2. at least two soft restarts in this episode raised (an accepted PUT means
   the controller is alive and gets its settle time)
3. the episode is at least `after_minutes` old; once two restarts have
   failed this is checked on every dark sample, so the plug moves when the
   age is reached rather than at the next restart's turn
4. fewer than `max_cycles_per_day` cycles in the rolling day
5. the plug answers, is the device recorded at setup, and reports its relay
   on (off means someone switched it off on purpose)

With `cycle` false (the default) the log says "would cycle" once per
episode and nothing moves. With it true: off, `off_seconds`, on, then
`settle_minutes` in which nothing is judged; a second cycle needs the whole
ladder again. An episode ends at the first successful sample.
"""
import datetime
import re
import time
from collections import deque

# The watchdog's own lines in the event log, read back at start so the daily caps survive a service restart.
# A failed attempt counts: check() takes the restart slot before the PUT goes out. A hand cycle (`dashboard:`) never did.
SEED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (watchdog: restart (?:#\d+ sent|attempt failed)|power: cycled #\d+ (?:today|in 24 h))")

# A hold: the owner said the miner will be unreachable on purpose. Nothing is judged until it is back
# (HOLD_OK_SAMPLES good samples in a row), the hold expires, or it is released. Its lines are read back
# at start like the caps, so a service restart mid-outage does not wake the ladder.
HOLD_OK_SAMPLES = 2
HOLD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) hold: (?:started by (you|the schedule)"
                     r"(?: until (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})|, no expiry)(?: \((.*)\))?|(released|expired))")
STAMP = "%Y-%m-%d %H:%M:%S"


def _parse_stamp(text):
    try:
        return datetime.datetime.strptime(text, STAMP).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


class Watchdog:
    def __init__(self, restart, events, interval, stall_minutes=5, unreachable_minutes=2,
                 min_gap_minutes=10, max_restarts_per_day=6, clock=time.time,
                 plug=None, power=None, sleep=time.sleep):
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
        # the power rung
        self.plug = plug
        self.power = dict(power or {})
        self._sleep = sleep
        self.episode_start = None           # time of the first failed sample after the last good one
        self.episode_failed_restarts = 0    # soft restarts that raised in this episode (reset by a cycle too)
        self._cycle_times = deque()
        # timestamps already counted by seed_from_events, so a second call over the same or overlapping
        # tail does not double-count (there is one call site today, at service start, but it must be safe)
        self._seeded_restarts = set()
        self._seeded_cycles = set()
        self.power_gap_until = None
        self._power_logged = False          # one "would cycle" / refusal / cap line per episode
        self._boot_check = None             # {at, cycle, retry} after a cycle: did the controller come up at all?
        self.last_power_reason = None
        self.hold = None                    # None, or {since, until (None: no expiry), reason, source, ok_streak}

    def observe(self, ok, accepted, t=None, hashing=None):
        """One sample. `hashing` says whether the miner reported a hashrate; a hold releases only on
        HOLD_OK_SAMPLES hashing samples in a row. On 2026-09-13 a controller came back from a power-on
        without its hashboard, answered HTTP with a zero hashrate, and two answers released the hold.
        None (older callers) means "same as ok". The post-cycle boot check clears the same way: an HTTP-only
        answer with a dead hashboard is exactly the failure it exists to catch, so it is only cancelled on a
        sample that is actually hashing (2026-09-18)."""
        t = self._clock() if t is None else t
        self._rows.append((t, bool(ok), accepted))
        if ok:
            self.episode_start = None
            self.episode_failed_restarts = 0
            self._power_logged = False
            if hashing is None or hashing:
                self._boot_check = None     # only a hashing sample proves it booted; ok alone does not (2026-09-18)
        elif self.episode_start is None:
            self.episode_start = t
        if self.hold is not None:
            back = bool(ok) if hashing is None else bool(ok and hashing)
            self.hold["ok_streak"] = self.hold["ok_streak"] + 1 if back else 0
            if self.hold["ok_streak"] >= HOLD_OK_SAMPLES:
                self.hold_release("back")

    # ------------------------------------------------------------ holds

    def _stamp(self, t):
        return datetime.datetime.fromtimestamp(t).strftime(STAMP)

    def hold_start(self, minutes=None, reason="", source="page"):
        """Stand down until the miner is back, `minutes` pass (None: never), or a release. Replaces a running hold."""
        now = self._clock()
        until = None if minutes is None else now + float(minutes) * 60
        self.hold = {"since": now, "until": until, "reason": reason or "", "source": source, "ok_streak": 0}
        who = "the schedule" if source == "schedule" else "you"
        when = ("until " + self._stamp(until)) if until is not None else ", no expiry"
        self._events.write("hold: started by %s%s%s%s" % (who, "" if until is None else " ", when,
                                                          (" (%s)" % reason) if reason else ""))
        return self.hold

    def hold_release(self, how):
        """End the hold: `how` is "you" (asked), "back" (the miner answered), or "expired". Clears the sample window."""
        if self.hold is None:
            return
        minutes = round((self._clock() - self.hold["since"]) / 60)
        if how == "back":
            self._events.write("hold: released, miner hashing again after %d min" % minutes)
        elif how == "expired":
            self._events.write("hold: expired after %d min with the miner still unreachable; watchdog resumed" % minutes)
        else:
            self._events.write("hold: released by you")
        self.hold = None
        self._rows.clear()

    def hold_info(self):
        """The running hold for /api/health, JSON-safe, or None."""
        h = self.hold
        if h is None:
            return None
        left = None if h["until"] is None else max(0, round((h["until"] - self._clock()) / 60))
        return {"since": self._stamp(h["since"]), "until": None if h["until"] is None else self._stamp(h["until"]),
                "reason": h["reason"], "source": h["source"], "minutes_left": left, "ok_streak": h["ok_streak"]}

    def _seed_hold(self, lines):
        """Restore the newest hold from the log's own lines unless a later line ended it or it has expired."""
        latest = None
        for line in lines:
            m = HOLD_RE.match(line or "")
            if not m:
                continue
            if m.group(5):
                latest = None
                continue
            t = _parse_stamp(m.group(1))
            until = _parse_stamp(m.group(3)) if m.group(3) else None
            if t is None or (m.group(3) and until is None):
                continue
            latest = {"since": t, "until": until, "reason": m.group(4) or "",
                      "source": "schedule" if m.group(2) == "the schedule" else "page", "ok_streak": 0}
        if latest is None or (latest["until"] is not None and latest["until"] <= self._clock()):
            return False
        self.hold = latest
        self._events.write("service: hold picked up from the event log (%s): nothing judged until the miner is back"
                           % ("until " + self._stamp(latest["until"]) if latest["until"] is not None else "no expiry"))
        return True

    def restarts_today(self):
        cutoff = self._clock() - 86400
        while self._restart_times and self._restart_times[0] < cutoff:
            self._restart_times.popleft()
        return len(self._restart_times)

    def cycles_today(self):
        cutoff = self._clock() - 86400
        while self._cycle_times and self._cycle_times[0] < cutoff:
            self._cycle_times.popleft()
        return len(self._cycle_times)

    def seed_from_events(self, lines):
        """Load the rolling caps from the event log's own lines (the log's tail, oldest first).

        Without this a service restart handed the watchdog a fresh day: on
        2026-09-12 a restart onto a hung miner ran a fourth cycle the cap
        would have blocked. Only the last 24 h count. The newest seeded
        restart or cycle also holds its settle gap. Returns (restarts, cycles).

        Idempotent by timestamp: a line whose stamp was already seeded by an
        earlier call is skipped, so calling this twice over the same or an
        overlapping tail does not double-count (measured: seeding the same
        two-cycle tail twice used to give cycles_today() == 4). The returned
        counts, and the pickup line below, only ever reflect what this call
        actually added.
        """
        cutoff = self._clock() - 86400
        restarts, cycles = [], []
        for line in lines:
            m = SEED_RE.match(line or "")
            if not m:
                continue
            try:
                t = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except (ValueError, OverflowError, OSError):
                continue
            if t < cutoff:
                continue
            if m.group(2).startswith("power:"):
                if t not in self._seeded_cycles:
                    self._seeded_cycles.add(t)
                    cycles.append(t)
            elif t not in self._seeded_restarts:
                self._seeded_restarts.add(t)
                restarts.append(t)
        self._restart_times.extend(sorted(restarts))
        self._cycle_times.extend(sorted(cycles))
        if restarts:
            self.last_restart = max(self.last_restart or 0, max(restarts))
        if cycles:
            self.power_gap_until = max(self.power_gap_until or 0, max(cycles) + float(self.power.get("settle_minutes", 0)) * 60)
        if restarts or cycles:
            plural = lambda n, w: "%d %s%s" % (n, w, "" if n == 1 else "s")
            self._events.write("service: watchdog picked up %s and %s from the last 24 h of the event log; the daily caps carry on"
                               % (plural(len(restarts), "restart"), plural(len(cycles), "cycle")))
        self._seed_hold(lines)
        return len(restarts), len(cycles)

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
        if self.power_gap_until is not None:
            floor = max(floor, self.power_gap_until)
        rows = list(self._rows)
        if not rows or rows[-1][0] <= floor or now - rows[-1][0] > 2 * self.interval:
            return None                     # nothing since the gap ended, or the newest sample is stale
        # unreachable: its own window, which may reach back into the gap. A restart that worked shows as good
        # samples well inside min_gap (a soft restart takes 60-90 s), so a dark tail at the gap's end is the verdict;
        # waiting for a further stall window on top cost 5 min per rung until 2026-09-13.
        if self._tail_dark(now):
            return self._unreachable_reason()
        # stalled: a full window of fresh samples after the gap, so a reboot's own quiet counter is never judged
        recent = [r for r in rows if r[0] > floor][-self.stall_rows:]
        if len(recent) < self.stall_rows or not self._contiguous(recent):
            return None
        if all(r[1] for r in recent) and len({r[2] for r in recent}) == 1 and recent[0][2] is not None:
            return "accepted shares frozen for %d min" % round(self.stall_rows * self.interval / 60)
        return None

    def _unreachable_reason(self):
        return "miner unreachable for %d min" % round(self.err_rows * self.interval / 60)

    def _tail_dark(self, now):
        """The last `unreachable_minutes` of samples all failed, with no gap among them and the newest fresh."""
        rows = list(self._rows)
        if len(rows) < self.err_rows or now - rows[-1][0] > 2 * self.interval:
            return False
        tail = rows[-self.err_rows:]
        return self._contiguous(tail) and all(not r[1] for r in tail)

    def _contiguous(self, rows):
        """No gap in these samples (the PC slept, the service paused): the span fits their count with slack."""
        return rows[-1][0] - rows[0][0] <= (len(rows) - 1) * self.interval * 1.5

    def check(self):
        """Judge the samples seen so far; send a restart if one is due. Returns the reason or None."""
        if self.hold is not None:
            if self.hold["until"] is not None and self._clock() >= self.hold["until"]:
                self.hold_release("expired")
            return None
        self._check_boot()
        reason = self.diagnose()
        self.last_reason = reason
        if not reason:
            self._capped_logged = False
            # Two restarts already failed this episode: the rung waits only on `after_minutes` now, on any dark
            # sample, not on the next restart's turn (which is a whole gap away when after_minutes is the longer).
            # A rung refused once this episode (plug silent or off, dry run, the cap) is not asked again until
            # the next restart's turn: the plug is queried once per rung, not every sample for the outage.
            if self.episode_failed_restarts >= 2 and not self._power_logged and self._tail_dark(self._clock()):
                self._consider_power(self._unreachable_reason())
            return None
        if self.restarts_today() >= self.max_restarts:
            if not self._capped_logged:
                self._events.write("watchdog: %s, but %d restarts in 24 h is the cap; not restarting"
                                   % (reason, self.max_restarts))
                self._capped_logged = True
            self._consider_power(reason)
            return reason
        now = self._clock()
        self.last_restart = now
        self._restart_times.append(now)
        try:
            self._restart()
            self._events.write("watchdog: restart #%d sent (%s)" % (self.restarts_today(), reason))
        except Exception as e:
            self.episode_failed_restarts += 1
            self._events.write("watchdog: restart attempt failed: %s (%s)" % (e, reason))
        self._consider_power(reason)
        return reason

    # ------------------------------------------------------------ the power rung

    def _power_once(self, line):
        if not self._power_logged:
            self._events.write(line)
            self._power_logged = True

    def _consider_power(self, reason):
        if self.plug is None or not reason.startswith("miner unreachable"):
            return
        if self.episode_failed_restarts < 2 or self.episode_start is None:
            return
        now = self._clock()
        if now - self.episode_start < float(self.power["after_minutes"]) * 60:
            return
        cap = int(self.power["max_cycles_per_day"])
        if self.cycles_today() >= cap:
            self._power_once("power: would cycle (%s), but %d cycles in 24 h is the cap; not cycling" % (reason, cap))
            return
        try:
            info = self.plug.identify()
            if info.get("device_id") != self.power.get("device_id"):
                self._power_once("power: the plug is not the configured device (id differs); not cycling")
                return
            if not self.plug.state():
                self._power_once("power: plug is off (someone switched it off); not cycling")
                return
            w = self.plug.watts() if info.get("meter") else None
        except Exception as e:
            self._power_once("power: plug did not answer (%s); not cycling" % e)
            return
        before = ("%.0f W before" % w) if w is not None else "no meter"
        if not self.power.get("cycle"):
            self._power_once("power: would cycle now (%s; %s); dry run, set \"cycle\": true in config.json to arm"
                             % (reason, before))
            return
        self._cycle(reason, before)

    def _cycle(self, reason, before, retry=False):
        """Open the relay and close it, start the settle gap, and book the boot check."""
        now = self._clock()
        self.last_power_reason = reason
        self._cycle_times.append(now)
        self.power_gap_until = now + float(self.power["settle_minutes"]) * 60
        self.episode_failed_restarts = 0
        self._power_logged = False
        self._boot_check = None
        try:
            self.plug.cycle(int(self.power["off_seconds"]), sleep=self._sleep)
            self._events.write("power: cycled #%d in 24 h: off %d s, on (%s; %s)"
                               % (self.cycles_today(), int(self.power["off_seconds"]), reason, before))
        except Exception as e:
            self._events.write("power: cycle failed: %s (%s; %s)" % (e, reason, before))
            return
        minutes = int(self.power.get("boot_check_minutes", 0) or 0)
        if minutes > 0:
            self._boot_check = {"at": self._clock() + minutes * 60, "cycle": self.cycles_today(), "retry": retry}

    def _check_boot(self):
        """`boot_check_minutes` after a cycle: is the controller drawing power at all? A cycle that leaves the
        wall under `boot_watts` never booted (2026-09-15 06:40: 12 W for 25 minutes, below even the hung
        controller's 34 W, until the ladder's next cycle). One repeat cycle, at once, within the daily cap; a
        second failure is logged and left to the ladder."""
        bc = self._boot_check
        if bc is None or self._clock() < bc["at"] or self.plug is None:
            return
        self._boot_check = None
        try:
            info = self.plug.identify()
            w = self.plug.watts() if info.get("meter") else None
        except Exception as e:
            self._events.write("power: boot check after cycle #%d skipped: plug did not answer (%s)" % (bc["cycle"], e))
            return
        if w is None or w >= float(self.power.get("boot_watts", 0)):
            return
        why = "controller did not come up after cycle #%d: %.0f W after %d min" % (bc["cycle"], w, int(self.power["boot_check_minutes"]))
        if bc["retry"]:
            self._events.write("power: %s; already cycled again once, leaving it to the ladder" % why)
            return
        cap = int(self.power["max_cycles_per_day"])
        if self.cycles_today() >= cap:
            self._events.write("power: %s, but %d cycles in 24 h is the cap; not cycling" % (why, cap))
            return
        self._cycle(why, "%.0f W before" % w, retry=True)
