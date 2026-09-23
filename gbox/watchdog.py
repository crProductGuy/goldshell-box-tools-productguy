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
# 0.10.0: how many polls a verdict waits for a read of the miner's log newer than itself, at most
EVIDENCE_WAIT_POLLS = 3
# 0.10.0 review: the accepted counter must rise on samples at least this far apart before the pool counts as back.
# One bump is not enough (two "Accepted" lines 8 min into the 2026-09-22 outage, then silence); a pool that is
# really back moves it every 10-40 s (same measurement).
SHARES_BACK_SECONDS = 60
# 0.10.0: how often the LAN link is asked while it matters (on Windows each answer is a PowerShell run)
LAN_CHECK_SECONDS = 60
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
                 plug=None, power=None, sleep=time.sleep, absent_minutes=2, upstream_restart_hours=8):
        self._restart = restart
        self._events = events
        self.interval = float(interval)
        self.stall_rows = max(2, round(stall_minutes * 60 / self.interval))
        self.err_rows = max(1, round(unreachable_minutes * 60 / self.interval))
        self.absent_rows = max(2, round(absent_minutes * 60 / self.interval)) if absent_minutes else 0   # 0: rule off
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
        self._gap_streak = 0                # hashing samples in a row since the gap started; ends it early
        self._power_logged = False          # one "would cycle" / refusal / cap line per episode
        self._boot_check = None             # {at, cycle, retry, confirmed, http_ok}: did the box come back?
        self.last_power_reason = None
        self.hold = None                    # None, or {since, until (None: no expiry), reason, source, ok_streak}
        # 0.10.0 B: the pool. Fed by `observe_log` from each read of the miner's log; see `_upstream_hold`.
        self.log_reader = False             # the poller reads the log (syslog_interval > 0); False: B is off
        self.log_wanted = False             # a rule is waiting for a read newer than its verdict
        self.upstream_restart_hours = float(upstream_restart_hours or 0)
        self._log_read_at = None            # when the last read was attempted, and whether it answered
        self._log_ok = False
        self._pool_down = False             # a pool line read since the accepted counter last moved, no fault after
        self._run_probing = False           # since the newest process start: probing for a pool ...
        self._run_accepted = False          # ... and not one share accepted
        self._last_accepted = None
        self._first_rise = None             # when the counter first rose since the pool evidence last changed
        self._verdict = None                # (kind, since): the rule waiting on the log, and since when
        self.upstream_since = None          # the running upstream episode, or None
        self._upstream_restarted = False
        # 0.10.0 E: can the miner be seen at all? The poller hands in each poll's plug reading (`observe_plug`);
        # `lan_check` is a callable, True/False/None, for this machine's link to the miner's LAN (netiface).
        self.lan_check = None
        self._plug_seen = None              # None: not told; True/False: the plug answered this poll
        self._plug_watts = None
        self._lan = (None, None)            # (checked at, answer): asked at most once a LAN_CHECK_SECONDS
        self.dark_since = None              # the running "can't see" episode, or None
        self._dark_why = None

    def observe(self, ok, accepted, t=None, hashing=None, absent=None):
        """One sample. `hashing` says whether the miner reported a hashrate; a hold releases only on
        HOLD_OK_SAMPLES hashing samples in a row. On 2026-09-13 a controller came back from a power-on
        without its hashboard, answered HTTP with a zero hashrate, and two answers released the hold.
        None (older callers) means "same as ok". The post-cycle boot check clears the same way: an HTTP-only
        answer with a dead hashboard is exactly the failure it exists to catch, so it is only cancelled on a
        sample that is actually hashing (2026-09-18).

        The settle gap after a cycle ends the same way, on HOLD_OK_SAMPLES hashing samples (2026-09-19):
        waiting out a fixed timer on a miner that is demonstrably back is what let `settle_minutes` 20 hide a
        dead hashboard for 22 minutes. The wall meter is never consulted here. It is optional hardware, and on
        2026-09-18 it read 15 W for a minute while the miner hashed above 600,000, so letting it gate a release
        would stand the watchdog down on a sensor glitch.

        `absent` (0.9.0) says the controller answered with no hashboard behind it: clock 0 and the board
        sensor at its no-sensor value. The caller decides that, from a model whose profile says the signature
        is verified; None (older callers, other models) is never absent. See `diagnose`."""
        t = self._clock() if t is None else t
        self._rows.append((t, bool(ok), accepted, bool(ok and absent)))
        if ok and accepted is not None:
            if self._last_accepted is not None and accepted > self._last_accepted:
                self._counter_rose(t)
            elif self._last_accepted is not None and accepted < self._last_accepted:
                self._first_rise = None     # a restart set it back to 0: that is not a share (0.10.0 review, H1)
            self._last_accepted = accepted
        back = bool(ok) if hashing is None else bool(ok and hashing)
        if ok:
            self.episode_start = None
            self.episode_failed_restarts = 0
            self._power_logged = False
            if hashing is None or hashing:
                self._boot_check = None     # only a hashing sample proves it booted; ok alone does not (2026-09-18)
            elif self._boot_check is not None:
                # It answered but the board is silent. The check runs on, remembering that the controller is at
                # least on the network: that is what picks the remedy, with no meter needed (2026-09-19).
                self._boot_check["http_ok"] = True
        elif self.episode_start is None:
            self.episode_start = t
        if self.hold is not None:
            self.hold["ok_streak"] = self.hold["ok_streak"] + 1 if back else 0
            if self.hold["ok_streak"] >= HOLD_OK_SAMPLES:
                self.hold_release("back")
        if self.power_gap_until is not None:
            self._gap_streak = self._gap_streak + 1 if back else 0
            if self._gap_streak >= HOLD_OK_SAMPLES:
                self._end_gap("the miner is hashing again")

    def _end_gap(self, why):
        """End the post-cycle settle gap early, on proof of health or of a settled fault (2026-09-19)."""
        if self.power_gap_until is None:
            return
        self.power_gap_until = None
        self._gap_streak = 0
        self._events.write("power: settle gap ended early, %s; the watchdog is judging again" % why)

    # ------------------------------------------------------------ can the miner be seen (0.10.0 E)

    def observe_plug(self, answered, watts=None):
        """This poll's plug reading, from the poller (which reads the plug every poll already): no extra request."""
        self._plug_seen = bool(answered)
        self._plug_watts = watts if answered else None

    def _lan_up(self):
        if self.lan_check is None:
            return None
        now = self._clock()
        at, answer = self._lan
        if at is None or now - at >= LAN_CHECK_SECONDS:
            try:
                answer = self.lan_check()
            except Exception:
                answer = None               # a failed OS query says nothing about the LAN
            self._lan = (now, answer)
        return answer

    def _cant_see(self):
        """Why the miner cannot be judged from here, or None. Each reason means the path, not the miner: the plug
        sits on the same LAN, this machine's own link says it is off that LAN, or the meter shows working power."""
        if self.plug is not None and self._plug_seen is False:
            return "the smart plug does not answer either"
        if self.plug is not None and self._plug_watts is not None and \
                self._plug_watts >= float(self.power.get("idle_watts", 100)):
            return "the plug reads %.0f W, a working miner" % self._plug_watts
        if self._lan_up() is False:
            return "this computer's LAN link to the miner is down"
        return None

    def _end_dark(self, why):
        """The path is back. The ladder starts from zero: a full `unreachable_minutes` of fresh dark samples
        before anything is sent, with the episode's clock and failed-restart count reset."""
        self._events.write("watchdog: can see again after %d min (%s); the ladder starts from zero"
                           % (round((self._clock() - self.dark_since) / 60), why))
        self.dark_since = self._dark_why = None
        self._rows.clear()
        self.episode_start = None
        self.episode_failed_restarts = 0
        self._power_logged = False
        self._verdict = None

    def _dark_hold(self, reason):
        """True when an unreachable verdict must not act because the miner cannot be seen. Nothing is sent and
        nothing counted; one line when it begins, one when it ends."""
        if not reason.startswith("miner unreachable"):
            return False
        why = self._cant_see()
        if why is None:
            return False
        if self.dark_since is None:
            self.dark_since = self._clock()
            self._dark_why = why
            self._events.write("watchdog: can't see the miner (%s); nothing sent and nothing counted until it "
                               "can" % why)
        return True

    # ------------------------------------------------------------ the pool (0.10.0 B)

    def observe_log(self, ok, signals=(), t=None):
        """One read of the miner's log: `ok` whether the web backend answered it, `signals` the read's pool
        evidence in log order (`api.classify_syslog`). A pool line stands until the accepted counter moves or a
        line that points at the miner itself comes after it, or a first read shows a minute of shares after it
        ("shares", 0.10.1); a process start begins a new run's evidence."""
        self._log_read_at = self._clock() if t is None else t
        self._log_ok = bool(ok)
        self.log_wanted = False
        for s in signals:
            if s == "pool":
                self._pool_down = True
                self._first_rise = None
            elif s == "fault":
                self._pool_down = self._run_probing = False
            elif s == "start":
                self._pool_down = self._run_probing = self._run_accepted = False
                self._first_rise = None
            elif s == "probing":
                self._run_probing = True
            elif s == "accepted":
                self._run_accepted = True
            elif s == "shares":                    # a first read: shares flowed for a minute after the pool line
                self._pool_down = False
                self._run_accepted = True
                self._first_rise = None

    def _counter_rose(self, t):
        """The accepted counter went up. The pool counts as back once it has gone up on two samples at least
        SHARES_BACK_SECONDS apart; one bump leaves the evidence standing. `_first_rise` stays set while shares
        flow, which is harmless: every path that sets pool evidence (a pool line, a process start) clears it."""
        if self._first_rise is None:
            self._first_rise = t
        if t - self._first_rise >= SHARES_BACK_SECONDS:
            self._shares_moved(t)

    def _shares_moved(self, t):
        """The accepted counter moved: the pool is taking shares, so any pool evidence is spent."""
        self._pool_down = False
        self._run_accepted = True
        if self.upstream_since is not None:
            self._events.write("watchdog: pool back, shares accepted again after %d min; judging as usual"
                               % round((t - self.upstream_since) / 60))
        self.upstream_since = None
        self._upstream_restarted = False

    def _upstream(self, kind):
        """The pool, not the miner, explains `kind`. A stall needs pool evidence; an unreachable miner also needs
        the web backend to have answered the newest read (measured 2026-09-22: port 4028 dies about 8 min into an
        outage while the backend keeps serving; on the 09-21 and 09-22 hangs the backend was dead too)."""
        evidence = self._pool_down or (self._run_probing and not self._run_accepted)
        return evidence and (kind == "stall" or self._log_ok)

    def _upstream_hold(self, reason):
        """True when `check` must not act on `reason` now: a read newer than the verdict is still to come, or the
        pool explains it. A verdict waits for a read taken after it (at most EVIDENCE_WAIT_POLLS polls, then it is
        judged on what there is), because the log is read every `syslog_interval`, and a pool line written after
        the last read would otherwise come too late. After `upstream_restart_hours` of one upstream episode, one
        soft restart is let through, counted against the cap; then none until the accepted counter moves."""
        if not self.log_reader:
            return False
        if reason.startswith("accepted shares frozen"):
            kind = "stall"
        elif reason.startswith("miner unreachable"):
            kind = "unreachable"
        else:
            return False
        now = self._clock()
        if self._verdict is None or self._verdict[0] != kind:
            self._verdict = (kind, now)
        if (self._log_read_at is None or self._log_read_at < self._verdict[1]) and \
                now - self._verdict[1] < EVIDENCE_WAIT_POLLS * self.interval:
            self.log_wanted = True
            return True
        if not self._upstream(kind):
            self.upstream_since = None
            return False
        if self.upstream_since is None:
            self.upstream_since = now
            self._upstream_restarted = False
            self._events.write("watchdog: pool unreachable; not restarting a miner that is waiting on its pool (%s)"
                               % reason)
        hours = self.upstream_restart_hours
        if hours > 0 and not self._upstream_restarted and now - self.upstream_since >= hours * 3600:
            self._upstream_restarted = True
            self._events.write("watchdog: pool unreachable for %g h; one soft restart, then none until shares are "
                               "accepted again" % hours)
            return False
        return True

    # ------------------------------------------------------------ holds

    def _stamp(self, t):
        return datetime.datetime.fromtimestamp(t).strftime(STAMP)

    def hold_start(self, minutes=None, reason="", source="page"):
        """Stand down until the miner is back, `minutes` pass (None: never), or a release. Replaces a running hold."""
        now = self._clock()
        until = None if minutes is None else now + float(minutes) * 60
        self.hold = {"since": now, "until": until, "reason": reason or "", "source": source, "ok_streak": 0}
        # A hold means hands off the hardware, and check() returns before reaching the boot check, so a pending
        # one would otherwise sit there and fire late when the hold lifts. The early return is right; the stale
        # check is not (2026-09-19).
        self._boot_check = None
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
        if self.hold is not None and self.hold["since"] == latest["since"]:
            return False                    # already restored from this same line: keep the live ok_streak,
                                            # which a second seed would otherwise reset and delay the release
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

        Idempotent by timestamp: a stamp already counted -- by an earlier call
        to this method, or by a live restart or cycle this process performed --
        is skipped, so a second call over the same or an overlapping tail does
        not double-count (measured: seeding the same two-cycle tail twice used
        to give cycles_today() == 4, and re-seeding the line _cycle had just
        written counted that cycle twice). The returned counts, and the pickup
        line below, only ever reflect what this call actually added.

        There is one call site, at service start, before anything live has
        happened; the idempotency is here so that stays a fact about the call
        site rather than a load-bearing assumption of this method.
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
        # Both deques must stay oldest-first. restarts_today()/cycles_today() prune with popleft and stop at
        # the first entry inside the window, so a single out-of-order old entry is never pruned and inflates
        # the count for good. A seeded line can be older than a live one already in the deque, so re-sort
        # rather than append.
        self._restart_times = deque(sorted(list(self._restart_times) + restarts))
        self._cycle_times = deque(sorted(list(self._cycle_times) + cycles))
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
        # hashboard absent (0.9.0): the controller answers, clock 0, board sensor at its no-sensor value. In 15
        # days of one SC-BOX's log this state never once ended by itself (6 episodes, 6 to 22 min, each until the
        # stall rule below got there), while 12 reset bursts, which look nothing like it, all healed inside
        # 2.5 min. So it gets its own short window and the stall window stays long. Judged only on samples after
        # the gap, like the stall rule, and only ever a soft restart: `_consider_power` takes no reason but
        # "miner unreachable". Nothing else gets a shorter window: of the soft restarts sent in those 15 days
        # to a board that was still present, 3 of 4 lost the board, where an absent one had nothing to lose.
        if self.absent_rows:
            tail = [r for r in rows if r[0] > floor][-self.absent_rows:]
            if len(tail) == self.absent_rows and self._contiguous(tail) and all(r[3] for r in tail):
                return "hashboard absent for %d min" % round(self.absent_rows * self.interval / 60)
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
        if self.dark_since is not None:
            rows = self._rows
            if rows and rows[-1][1]:
                self._end_dark("the miner answered")
            elif self._cant_see() is None:
                self._end_dark("the path is back, the miner is still silent")
        reason = self.diagnose()
        self.last_reason = reason
        if not reason:
            self._verdict = None
            self.log_wanted = False
            self._capped_logged = False
            # Two restarts already failed this episode: the rung waits only on `after_minutes` now, on any dark
            # sample, not on the next restart's turn (which is a whole gap away when after_minutes is the longer).
            # A rung refused once this episode (plug silent or off, dry run, the cap) is not asked again until
            # the next restart's turn: the plug is queried once per rung, not every sample for the outage.
            if self.episode_failed_restarts >= 2 and not self._power_logged and self._tail_dark(self._clock()) \
                    and self.upstream_since is None and self.dark_since is None:
                self._consider_power(self._unreachable_reason())
            return None
        if self._dark_hold(reason):
            return None
        if self._upstream_hold(reason):
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
        self._seeded_restarts.add(now)      # as in _cycle: this restart's own log line must not be re-counted
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
        # 0.10.0 C: the meter can only prevent a cycle, never add one. At or over idle_watts the miner is working
        # and the path to it is down; under unpowered_watts with the relay on nothing is drawing power behind the
        # plug (a cord out downstream, the miner's own switch). Between is the hung controller (about 34 W).
        if w is not None and w >= float(self.power.get("idle_watts", 100)):
            self._power_once("power: the plug reads %.0f W, a working miner; the path to it is down, not the "
                             "miner (%s); not cycling" % (w, reason))
            return
        unpowered = float(self.power.get("unpowered_watts", 0) or 0)
        if w is not None and w < unpowered:
            self._power_once("power: the plug reads %.0f W with its relay on: nothing is drawing power behind it "
                             "(%s); not cycling" % (w, reason))
            return
        before = ("%.0f W before" % w) if w is not None else "no meter"
        if not self.power.get("cycle"):
            self._power_once("power: would cycle now (%s; %s); dry run, set \"cycle\": true in config.json to arm"
                             % (reason, before))
            return
        self._cycle(reason, before)

    def _cycle(self, reason, before, retry=False):
        """Open the relay and close it, start the settle gap, and book the boot check.

        Both timers run from the moment power comes BACK, not from the relay opening (2026-09-19).
        `plug.cycle()` blocks for `off_seconds`, so timing the gap from here spent that whole time inside it:
        at `off_seconds` 120 a `settle_minutes` of 6 was really 4, silently, and disagreed with what
        `seed_from_events` recomputes from the event line (which is written after the cycle, so it already
        carries the power-return time). The provisional value below still covers `plug.cycle()` raising.

        `_cycle_times` deliberately keeps the relay-open time: it feeds a 24-hour rate limit, where a
        difference of at most `off_seconds` is immaterial, and it must count a failed cycle too.
        """
        now = self._clock()
        self.last_power_reason = reason
        self._cycle_times.append(now)
        self._seeded_cycles.add(now)        # so a later seed of the line written below does not count it twice
        self.power_gap_until = now + float(self.power["settle_minutes"]) * 60
        self._gap_streak = 0
        self.episode_failed_restarts = 0
        self._power_logged = False
        self._boot_check = None
        try:
            self.plug.cycle(int(self.power["off_seconds"]), sleep=self._sleep)
            self.power_gap_until = self._clock() + float(self.power["settle_minutes"]) * 60
            self._events.write("power: cycled #%d in 24 h: off %d s, on (%s; %s)"
                               % (self.cycles_today(), int(self.power["off_seconds"]), reason, before))
        except Exception as e:
            self._events.write("power: cycle failed: %s (%s; %s)" % (e, reason, before))
            return
        minutes = int(self.power.get("boot_check_minutes", 0) or 0)
        if minutes > 0:
            self._boot_check = {"at": self._clock() + minutes * 60, "cycle": self.cycles_today(),
                                "retry": retry, "confirmed": False, "http_ok": False}

    def _check_boot(self):
        """`boot_check_minutes` after power returns: did the box come back, and which remedy does it need?

        The fault is decided on what the miner's own API shows, never on the meter: a plug that measures watts
        is optional hardware and must not be what the diagnosis rests on (2026-09-19). Two signals, both free:

        * Reaching here at all means the miner is NOT hashing -- `observe()` clears the check on a hashing
          sample, which is the whole point of the 2026-09-18 fix.
        * `http_ok` says whether it has answered at all since the cycle, which separates a controller that
          never came up from one that is up with a dead hashboard.

        The meter, when there is one, only refines the remedy. Silent on the network AND under `boot_watts`
        (or no meter to say otherwise) is a box that never powered up, and only another cycle will help
        (2026-09-15 06:40: 12 W for 25 minutes, below even the hung controller's 34 W). Anything else -- it
        answered, or it is drawing real power -- is a job for a soft restart first, so the settle gap ends and
        the ladder takes over, escalating on its own if the soft restarts fail.

        Either way the verdict waits for a second reading `boot_check_minutes` later. One low reading is not
        proof: `cli.py` tells the owner to allow two or three minutes on units other than this one, and every
        one of the five known cold-start failures transiently drew over `boot_watts` in its first 30 to 70
        seconds before collapsing. One repeat cycle at most, within the daily cap.
        """
        bc = self._boot_check
        if bc is None or self._clock() < bc["at"] or self.plug is None:
            return
        minutes = int(self.power.get("boot_check_minutes", 0) or 0)
        w = None
        try:
            info = self.plug.identify()
            w = self.plug.watts() if info.get("meter") else None
        except Exception as e:
            self._events.write("power: boot check after cycle #%d: the plug did not answer (%s); judging on the "
                               "miner's own behaviour instead" % (bc["cycle"], e))
        if not bc.get("confirmed"):
            self._boot_check = dict(bc, at=self._clock() + minutes * 60, confirmed=True)
            self._events.write("power: after cycle #%d the miner is not hashing at %d min (%s, %s); reading again "
                               "in %d min before acting"
                               % (bc["cycle"], minutes, "it has answered HTTP" if bc.get("http_ok") else "silent",
                                  ("%.0f W" % w) if w is not None else "no meter", minutes))
            return
        self._boot_check = None
        dark = not bc.get("http_ok")
        unpowered = w is None or w < float(self.power.get("boot_watts", 0))
        if not (dark and unpowered):
            self._events.write("power: after cycle #%d the miner is still not hashing at %d min (%s, %s), but that "
                               "is not a box without power; leaving it to the restart ladder"
                               % (bc["cycle"], 2 * minutes, "it has answered HTTP" if not dark else "silent",
                                  ("%.0f W" % w) if w is not None else "no meter"))
            self._end_gap("the boot check has decided a soft restart is the right remedy")
            return
        # The reading is carried separately: _cycle appends it to the "cycled #N" line already, and repeating
        # it inside the reason printed "no meter ... no meter" on a meterless plug (seen on the bench, 09-19).
        reading = ("%.0f W" % w) if w is not None else "no meter"
        why = ("controller did not come up after cycle #%d: silent on the network for %d min"
               % (bc["cycle"], 2 * minutes))
        if bc["retry"]:
            self._events.write("power: %s (%s); already cycled again once, leaving it to the ladder"
                               % (why, reading))
            return
        cap = int(self.power["max_cycles_per_day"])
        if self.cycles_today() >= cap:
            self._events.write("power: %s (%s), but %d cycles in 24 h is the cap; not cycling"
                               % (why, reading, cap))
            return
        self._cycle(why, reading, retry=True)
