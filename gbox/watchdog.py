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
import math
import re
import statistics
import time
from collections import deque

# The watchdog's own lines in the event log, read back at start so the daily caps survive a service restart.
# A failed attempt counts: check() takes the restart slot before the PUT goes out. A hand cycle (`dashboard:`) never did.
SEED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (watchdog: restart (?:#\d+ sent|attempt failed)|power: cycled #\d+ (?:today|in 24 h))")
# 0.13.0: the deadline's own cycles. A line form SEED_RE cannot match, so they never count against
# max_cycles_per_day after a restart (decision 3); seeded into their own deque, and into the settle gap.
DEADLINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) power: deadline cycle #\d+ in 24 h")
# 0.13.0: two samples further apart than this mean the PC slept or the service paused. The silence clock does
# not run across it: nobody watched, and a miner that recovered meanwhile must not be cut on waking (review A2).
SILENCE_GAP_SECONDS = 300

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
# 0.13.0 release review: failed log reads in a row before the deadline stops believing a pool wait. One is not
# enough: a single ~2 MB read can time out on a backend that is only waiting on its pool. A frozen controller fails
# every read, so at the 300 s default this costs the deadline 5 min at most, inside its 30.
LOG_FAILS_TO_END_POOL_WAIT = 2
# 0.10.0: how often the LAN link is asked while it matters (on Windows each answer is a PowerShell run)
LAN_CHECK_SECONDS = 60
HOLD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) hold: (?:started by (you|the schedule)"
                     r"(?: until (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})|, no expiry)(?: \((.*)\))?|(released|expired))")
STAMP = "%Y-%m-%d %H:%M:%S"
# 0.13.2: the weak-hashing rule (owner's decision 2026-10-29, numbers per model in `models.WEAK_HASHING`). A window
# is this many ok samples in a row, every one under the ceiling and taken at least WEAK_WARM_SECONDS into the
# miner's run: the replay over every log since September counted samples from 300 s of uptime, and the power settle
# gap ends about a minute after power returns, too soon for a slow warm-up.
WEAK_SAMPLES = 10
WEAK_WARM_SECONDS = 300
# An episode ends on a full window at strength at least this long after its last weak window. Five minutes alone
# would let a board that relapses 20 min after each restart start a new episode, and a new pair of restarts, each
# time (design review, 2026-10-29); no cured spell on record relapsed inside 2 h.
WEAK_QUIET_SECONDS = 3600
# Read back at start so a service restart does not hand a weak episode fresh restarts. The reason is the line's
# last parenthesis and carries none of its own; an attempt that failed has the exception's text before it.
WEAK_SEED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) watchdog: restart (?:#\d+ sent|attempt failed: .*) "
                          r"\(hashing weakly [^()]*\)$")
WEAK_END_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) watchdog: weak hashing ended ")


def _num(x):
    """A finite number from a sample's field, or None (the miner's JSON is untrusted; bool is not a number)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def _parse_stamp(text):
    try:
        return datetime.datetime.strptime(text, STAMP).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


class Watchdog:
    def __init__(self, restart, events, interval, stall_minutes=5, unreachable_minutes=2,
                 min_gap_minutes=10, max_restarts_per_day=6, clock=time.time,
                 plug=None, power=None, sleep=time.sleep, absent_minutes=2, upstream_restart_hours=8,
                 dead_minutes=0, weak_restarts=2):
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
        # 0.12.1: this outage has had its one no-load cycle. Cleared only by a sample the miner answers, never by
        # an episode reset: `_end_dark` restarts the episode on a one-poll plug or LAN miss, and keying this on
        # episode_start let a flap mid-outage buy a second cut (review of 0.12.1, reproduced).
        self._unpowered_cycled = False
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
        self._log_fails = 0                 # failed reads in a row; see LOG_FAILS_TO_END_POOL_WAIT
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
        # 0.13.0 deadline (design Option A): one clock per silence, from the first sample the controller did not
        # answer at all after the last one it did. Nothing about the path resets it (a plug blip, a LAN flap, the
        # meter): only an answer, or a gap in the samples.
        self.dead_minutes = float(dead_minutes or 0)
        self.silent_since = None
        # The last sample the controller answered, kept across sample gaps and holds; None until one in this run.
        # The deadline's back-off counts the cycles after it (release review 2), not those after `silent_since`,
        # which a gap or a hold moves on.
        self._last_answered = None
        self._deadline_times = deque()
        self._seeded_deadlines = set()
        self._deadline_logged = False       # one "power cycle at HH:MM" line per silence
        self._deadline_refused = None       # the refusal last written, so a repeat is not written every poll
        # 0.13.2: weak hashing. `_weak` holds the newest run of samples under the ceiling, (t, mhs, ceiling, warm,
        # accepted, the model's row, clock); `_weak_healthy` the times of the newest run at strength. Either is emptied by
        # a sample that breaks it: a failed one, one with no clock or rate, one under the model's lowest clock.
        self.weak_restarts = int(weak_restarts or 0)
        self._weak = deque(maxlen=WEAK_SAMPLES)
        self._weak_healthy = deque(maxlen=WEAK_SAMPLES)
        self.weak_since = None              # the running weak episode, or None
        self._weak_last = None              # its newest qualifying window
        self._weak_count = 0                # weak restarts taken in it (an attempt counts, like the daily cap)
        self._weak_logged = False           # its one "is the limit" line
        self._seeded_weak = set()
        self._weak_after = float("-inf")    # a hold's end: no weak window reaches back past it

    def observe(self, ok, accepted, t=None, hashing=None, absent=None, answered=None):
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
        is verified; None (older callers, other models) is never absent. See `diagnose`.

        `answered` (0.13.0, D2) says the controller replied at all, even with an HTTP error or a refused login:
        a failed sample that was answered is a live controller, never a silent one, and is not power-cycled.
        None (older callers) means "same as ok"."""
        t = self._clock() if t is None else t
        answered = bool(ok) if answered is None else bool(answered or ok)
        if self._rows and t - self._rows[-1][0] > SILENCE_GAP_SECONDS:
            self.silent_since = None        # nobody watched across the gap; start counting again (review A2)
        if answered:
            self.silent_since = None
            self._last_answered = t
            self._deadline_logged = False
            self._deadline_refused = None
        elif self.silent_since is None:
            self.silent_since = t
        self._rows.append((t, bool(ok), accepted, bool(ok and absent), answered))
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
            self._unpowered_cycled = False
            self._power_logged = False
            if hashing is None or hashing:
                self._boot_check = None     # only a hashing sample proves it booted; ok alone does not (2026-09-18)
            elif self._boot_check is not None:
                # It answered but the board is silent. The check runs on, remembering that the controller is at
                # least on the network: that is what picks the remedy, with no meter needed (2026-09-19).
                self._boot_check["http_ok"] = True
        else:
            self._weak.clear()              # the poller hands only ok samples to observe_rate (design review 7)
            self._weak_healthy.clear()
            if answered and self._boot_check is not None:
                self._boot_check["http_ok"] = True   # a 500 while booting is an answer too (review A7)
            if self.episode_start is None:
                self.episode_start = t
        if self.hold is not None:
            self.hold["ok_streak"] = self.hold["ok_streak"] + 1 if back else 0
            if self.hold["ok_streak"] >= HOLD_OK_SAMPLES:
                self.hold_release("back")
        if self.power_gap_until is not None:
            self._gap_streak = self._gap_streak + 1 if back else 0
            if self._gap_streak >= HOLD_OK_SAMPLES:
                self._end_gap("the miner is hashing again")

    def observe_rate(self, mhs, weak, clock=None, elapsed=None, t=None):
        """0.13.2: one ok sample's 20 s rate for the weak-hashing rule, called after `observe`. `weak` is the
        model's row from `models.weak_hashing_for` (None: the rule is off for this model). The ceiling is
        `ceiling_chips` chips' worth at the sample's clock; a sample under it joins the weak window, one at or over
        it the window at strength that ends an episode. Replaces decision 13's log-only line (the 10-20 hour and the
        10-29 night, 4 h at 4 chips of 16, were both spells this rule exists for)."""
        t = self._clock() if t is None else t
        mhs, clock, elapsed = _num(mhs), _num(clock), _num(elapsed)
        if not weak or mhs is None or clock is None or clock < weak["min_clock"]:
            self._weak.clear()              # nothing to judge on: a missing clock is not a guess (review 2, 3)
            self._weak_healthy.clear()
            return
        ceiling = weak["ceiling_chips"] * weak["chip_mhs_per_mhz"] * clock
        if mhs < ceiling:
            warm = elapsed is not None and elapsed >= WEAK_WARM_SECONDS
            # a non-finite counter is no counter: NaN <= NaN is False and would read as rising (review 2)
            self._weak.append((t, mhs, ceiling, warm, _num(self._last_accepted), weak, clock))
            self._weak_healthy.clear()
            return
        self._weak.clear()
        self._weak_healthy.append(t)
        if (self.weak_since is not None and len(self._weak_healthy) == WEAK_SAMPLES
                and self._contiguous([(x,) for x in self._weak_healthy])
                and t - (self._weak_last or self.weak_since) >= WEAK_QUIET_SECONDS):
            self._events.write("watchdog: weak hashing ended after %d min (%.0f GH/s); %d restart%s in the episode"
                               % (round((t - self.weak_since) / 60), mhs / 1000, self._weak_count,
                                  "" if self._weak_count == 1 else "s"))
            self.weak_since = self._weak_last = None
            self._weak_count = 0
            self._weak_logged = False

    def _weak_reason(self, floor):
        """The weak-hashing verdict, or None. A full window after `floor` (the restart and settle gaps), every
        sample warm and under the ceiling, the newest the newest sample seen, its median at or over the model's
        floor (under it is "not hashing", the other rules' business), and the accepted counter higher at its end
        than at its start (frozen is the stall rule's). Past `weak_restarts` in this episode it writes one line and
        returns None, so the idle path in `check` still runs (design review 5)."""
        rows = [r for r in self._weak if r[0] > max(floor, self._weak_after)]
        # The window's newest sample must be the newest sample seen. Matched within half a poll, not exactly: a
        # caller that stamps the two calls separately differs by microseconds, and exact equality made the rule
        # dead in the service (0.13.2 review 1).
        if len(rows) < WEAK_SAMPLES or not self._rows or abs(rows[-1][0] - self._rows[-1][0]) > self.interval / 2:
            return None
        if not self._contiguous(rows) or not all(r[3] for r in rows):
            return None
        median = statistics.median(r[1] for r in rows)
        first, last = rows[0][4], rows[-1][4]
        if median < rows[-1][5]["floor_mhs"] or first is None or last is None or last <= first:
            return None
        now = self._clock()
        if self.weak_since is None:
            self.weak_since = now
            self._weak_count = 0
            self._weak_logged = False
        self._weak_last = now
        mins = round(WEAK_SAMPLES * self.interval / 60)
        if self._weak_count >= self.weak_restarts:
            if not self._weak_logged:
                self._weak_logged = True
                self._events.write("watchdog: hashing weakly for %d min, median %.0f GH/s; %d weak restart%s in this "
                                   "episode is the limit, logged only, nothing sent until it hashes at strength "
                                   "for a full window an hour after its last weak one"
                                   % (mins, median / 1000, self.weak_restarts, "" if self.weak_restarts == 1 else "s"))
            return None
        return ("hashing weakly for %d min: median %.0f GH/s, under the %.0f GH/s of %d chips at %.0f MHz"
                % (mins, median / 1000, rows[-1][2] / 1000, rows[-1][5]["ceiling_chips"], rows[-1][6]))

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
        sits on the same LAN, this machine's own link says it is off that LAN, or the meter shows working power.
        This machine's link is asked first (0.13.0): with it down the plug cannot answer either, and only that
        reason makes `_end_dark` discard the samples, which failed on this side."""
        if self._lan_up() is False:
            return "this computer's LAN link to the miner is down"
        if self.plug is not None and self._plug_seen is False:
            return "the smart plug does not answer either"
        if self.plug is not None and self._plug_watts is not None and \
                self._plug_watts >= float(self.power.get("idle_watts", 100)):
            return "the plug reads %.0f W, a working miner" % self._plug_watts
        return None

    def _end_dark(self, why):
        """The path is back. After this computer's own LAN link was down, the ladder starts from zero: those
        samples failed on this side, so they say nothing about the miner, and a full `unreachable_minutes` of
        fresh dark samples is needed before anything is sent.

        After a plug that did not answer, or a meter at working power, the samples were the miner's own answers
        and still count (0.13.0, D1): until then a one-poll plug blip cleared the episode, so a miner hung through
        a flaky-plug hour was soft-restarted every few minutes and never cycled."""
        lan = (self._dark_why or "").startswith("this computer's LAN link")
        self._events.write("watchdog: can see again after %d min (%s); %s"
                           % (round((self._clock() - self.dark_since) / 60), why,
                              "the ladder starts from zero" if lan else "the ladder carries on"))
        self.dark_since = self._dark_why = None
        if lan:
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
        self._log_fails = 0 if ok else self._log_fails + 1
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

    def _pool_wait_stands(self, kind):
        """`_upstream(kind)`, except that a running pool wait survives one failed log read: it takes
        LOG_FAILS_TO_END_POOL_WAIT in a row to end it, so one flaky read cannot cut a waiting miner. Decides when
        `_upstream_hold` ends the episode and whether the deadline waits (release review 3: the hold cleared the
        episode on one failed read, reached when the meter reading dropped out mid-wait, and the deadline then
        cut)."""
        if self._upstream(kind):
            return True
        return (kind == "unreachable" and self.upstream_since is not None
                and self._log_fails < LOG_FAILS_TO_END_POOL_WAIT and self._upstream("stall"))

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
            # The ladder acts on this read as before (the 09-21 hang), but the episode the deadline asks about
            # ends only by `_pool_wait_stands`' rule (release review 3).
            if not self._pool_wait_stands(kind):
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
        self._weak_after = self._clock()    # 0.13.2: like the stall rule, a full weak window after the hold
        # The deadline's clock starts at the hold's end (design 4.1): it ran on through the hold, and a hold that
        # expired with the cable still out was cut 2 min later (release review 3).
        self.silent_since = None
        self._deadline_logged = False

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
        restarts, cycles, deadlines = [], [], []
        for line in lines:
            d = DEADLINE_RE.match(line or "")
            if d:                           # 0.13.0: its own deque; it feeds the settle gap but never the cap
                t = _parse_stamp(d.group(1))
                if t is not None and t >= cutoff and t not in self._seeded_deadlines:
                    self._seeded_deadlines.add(t)
                    deadlines.append(t)
                continue
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
        self._deadline_times = deque(sorted(list(self._deadline_times) + deadlines))
        if restarts:
            self.last_restart = max(self.last_restart or 0, max(restarts))
        if cycles or deadlines:
            self.power_gap_until = max(self.power_gap_until or 0,
                                       max(cycles + deadlines) + float(self.power.get("settle_minutes", 0)) * 60)
        if deadlines:
            self._events.write("service: watchdog picked up %d deadline cycle%s from the last 24 h of the event log"
                               % (len(deadlines), "" if len(deadlines) == 1 else "s"))
        if restarts or cycles:
            plural = lambda n, w: "%d %s%s" % (n, w, "" if n == 1 else "s")
            self._events.write("service: watchdog picked up %s and %s from the last 24 h of the event log; the daily caps carry on"
                               % (plural(len(restarts), "restart"), plural(len(cycles), "cycle")))
        self._seed_hold(lines)
        self._seed_weak(lines)
        return len(restarts), len(cycles)

    def _seed_weak(self, lines):
        """0.13.2: the weak restarts written after the newest "weak hashing ended" line in the tail are the running
        episode's, so a service restart does not hand it a fresh pair. No 24 h cutoff: an episode at its limit can
        stay weak longer than a day (review 3). A stale one costs nothing: it ends on the first window at strength,
        its last weak window being long past. Idempotent by timestamp, as the caps are; a failed attempt counts, as
        it does live (design review 6)."""
        weak, ended = [], None
        for line in lines:
            e = WEAK_END_RE.match(line or "")
            if e:
                t = _parse_stamp(e.group(1))
                if t is not None:
                    ended, weak = t, []
                continue
            m = WEAK_SEED_RE.match((line or "").rstrip("\r\n"))
            if m:
                t = _parse_stamp(m.group(1))
                if t is not None:
                    weak.append(int(t))
        new = [t for t in weak if t not in self._seeded_weak and (ended is None or t > ended)]
        if not new:
            return
        self._seeded_weak.update(new)
        if self.weak_since is None:
            self.weak_since = min(weak)
            self._weak_logged = False
        self._weak_count += len(new)
        self._weak_last = max(self._weak_last or 0, max(new))
        self._events.write("service: watchdog picked up a weak-hashing episode from the event log: %d weak "
                           "restart%s since %s" % (self._weak_count, "" if self._weak_count == 1 else "s",
                                                   self._stamp(self.weak_since)))

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
        # weak hashing (0.13.2): before the stall rule's own window test, which would otherwise gate it on
        # `stall_rows` (design review 8). The two cannot both hold: weak needs the counter to rise, stall frozen.
        weak = self._weak_reason(floor)
        if weak:
            return weak
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

    def _tail_silent(self, now):
        """As `_tail_dark`, but not one of those samples was answered at all (0.13.0, D2)."""
        return self._tail_dark(now) and not any(r[4] for r in list(self._rows)[-self.err_rows:])

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
        if self._consider_deadline():
            self.last_reason = "silent for %d min (deadline)" % round(self.dead_minutes)
            return self.last_reason
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
        if reason.startswith("hashing weakly"):
            # Counted when the slot is taken, like the cap. Never the power rung: `_consider_power` takes only
            # "miner unreachable". A PUT that raises does add to `episode_failed_restarts`, as a stall's does.
            self._weak_count += 1
            self._seeded_weak.add(int(now))     # the log's stamp is whole seconds (review 4)
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
        if not self._tail_silent(now):
            # 0.13.0 D2: the controller answers (an HTTP error, a refused login), it only fails the sample. It is
            # alive, so a power cycle would not help: a wrong password would be cut every few minutes to the cap.
            self._power_once("power: the miner answers but fails every sample (%s); a power cycle would not help, "
                             "not cycling" % reason)
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
            # 0.12.1: no load is not always a cord out downstream. On 2026-10-26 the SC-BOX went from 147 W to 1 W
            # with no hang first, red light, no ARP, and sat 66 min until a hand cycle brought it straight back: a
            # supply that tripped and needed its input removed. So once the outage is unpowered_cycle_minutes old,
            # one cycle per outage; a box that is really unplugged costs one harmless cut and is then left alone.
            minutes = int(self.power.get("unpowered_cycle_minutes", 0) or 0)
            if minutes <= 0:
                self._power_once("power: the plug reads %.0f W with its relay on: nothing is drawing power behind it "
                                 "(%s); not cycling" % (w, reason))
                return
            if self._unpowered_cycled:
                self._power_once("power: the plug still reads %.0f W with its relay on after one cycle this outage "
                                 "(%s); not cycling again: check the miner's power by hand" % (w, reason))
                return
            if now - self.episode_start < minutes * 60:
                self._power_once("power: the plug reads %.0f W with its relay on: nothing is drawing power behind it "
                                 "(%s); one cycle at %d min, in case its supply tripped" % (w, reason, minutes))
                return
            before = "%.0f W before" % w
            if not self.power.get("cycle"):
                self._power_once("power: would cycle now (%s; %s, a supply that may have tripped); dry run, set "
                                 "\"cycle\": true in config.json to arm" % (reason, before))
                return
            self._unpowered_cycled = True
            self._cycle("%s; nothing drawing power for %d min, a supply that may have tripped" % (reason, minutes),
                        before, retry=True, unpowered=True)
            return
        before = ("%.0f W before" % w) if w is not None else "no meter"
        if not self.power.get("cycle"):
            self._power_once("power: would cycle now (%s; %s); dry run, set \"cycle\": true in config.json to arm"
                             % (reason, before))
            return
        self._cycle(reason, before)

    # ------------------------------------------------------------ the deadline (0.13.0, design Option A)

    def deadline_cycles_today(self):
        cutoff = self._clock() - 86400
        while self._deadline_times and self._deadline_times[0] < cutoff:
            self._deadline_times.popleft()
        return len(self._deadline_times)

    def _deadline_due(self):
        """When the next deadline cycle is due for the running silence: `dead_minutes` after it began, the second
        `dead_minutes` after the first, then one every `dead_retry_hours`. Counted from the cycles of EITHER kind
        since the controller last answered, and never sooner than `dead_minutes` after the last cycle of either
        kind, so a fast cycle or one from before a service restart is not followed by a cut moments later (review
        A3). Never sooner than `dead_minutes` into the running silence clock either, which a sample gap or a hold
        restarts (A2, design 4.1).

        Release review 2: keyed on `silent_since`, a service restart or a sample gap after two cuts found no cuts
        "since" and ran the 30/60 schedule again, two extra cuts per restart. A service that has not yet seen an
        answer in this run counts every cycle of the last 24 h: after a restart it may wait longer, never less."""
        dead = self.dead_minutes * 60
        self.cycles_today()
        self.deadline_cycles_today()        # both prune to the 24 h window
        times = [t for t in list(self._cycle_times) + list(self._deadline_times)]
        last = max(times) if times else None
        since = [t for t in times if self._last_answered is None or t >= self._last_answered]
        if not since:
            due = self.silent_since + dead
        elif len(since) == 1:
            due = max(since) + dead
        else:
            due = max(since) + float(self.power.get("dead_retry_hours", 4)) * 3600
        due = max(due, self.silent_since + dead)
        return max(due, last + dead) if last is not None else due

    def _deadline_refuse(self, line, key=None):
        """One line per kind of refusal until it changes; `key` names the kind when the line carries a count."""
        key = key or line
        if self._deadline_refused != key:
            self._events.write(line)
            self._deadline_refused = key

    def _consider_deadline(self):
        """Cycle a controller that has been silent for `dead_minutes`, whatever the meter reads. True if it cycled.

        The meter defers the cycle, it no longer holds it: three times (10-06, 10-11, 10-18) a frozen controller sat
        at full board power, which the 0.10.0 meter veto read as "a working miner", for 19 h 38 min, 477 and 115 min.
        Not counted against max_cycles_per_day, and still run when the fast rung has reached that cap (decisions 3
        and 3a): its own back-off is the loop guard. Never for a box drawing under `unpowered_watts`, which is the
        0.12.1 rule's alone (review A1), never while the pool is down, a hold runs, or the settle gap runs, and only
        on a fresh, unbroken window of samples the controller did not answer at all (review A2, D2)."""
        if not self.dead_minutes or self.plug is None or self.silent_since is None:
            return False
        now = self._clock()
        # The pool explains the silence only while the log still shows it AND the web backend answered the newest
        # read (the 09-22 signature: 4028 dead, web up). Not the episode alone: `upstream_since` is cleared by
        # `_upstream_hold`, which `_dark_hold` stops being reached at full power, so a freeze inside a pool
        # outage was never cut (release review 1). A frozen controller fails the log read on its next schedule;
        # it takes LOG_FAILS_TO_END_POOL_WAIT of them in a row (`_pool_wait_stands`).
        if self.upstream_since is not None and self._pool_wait_stands("unreachable"):
            return False
        if self.power_gap_until is not None and now < self.power_gap_until:
            return False
        if not self._tail_silent(now):
            return False
        due = self._deadline_due()
        if now < due:
            if self.dark_since is not None and not self._deadline_logged:
                self._deadline_logged = True
                self._events.write("watchdog: silent %d min and %s; power cycle at %s unless it answers"
                                   % (round((now - self.silent_since) / 60), self._dark_why,
                                      self._stamp(due)[11:16]))
            return False
        if self._lan_up() is False:         # the LAN first, as in `_cant_see`: with it down the plug is silent too
            self._deadline_refuse("power: deadline reached but this computer's LAN link is down; trying again "
                                  "each poll")
            return False
        if self._plug_seen is False:
            self._deadline_refuse("power: deadline reached but the plug does not answer; trying again each poll")
            return False
        try:
            info = self.plug.identify()
            if info.get("device_id") != self.power.get("device_id"):
                self._deadline_refuse("power: the plug is not the configured device (id differs); not cycling")
                return False
            if not self.plug.state():
                self._deadline_refuse("power: plug is off (someone switched it off); not cycling")
                return False
            w = self.plug.watts() if info.get("meter") else None
        except Exception as e:
            self._deadline_refuse("power: deadline reached but the plug did not answer (%s); trying again each "
                                  "poll" % e, key="plug error")
            return False
        unpowered = float(self.power.get("unpowered_watts", 0) or 0)
        if w is not None and w < unpowered:
            return False                    # the 0.12.1 no-load rule's case, and only its (review A1)
        minutes = round((now - self.silent_since) / 60)
        before = ("%.0f W before" % w) if w is not None else "no meter"
        reason = "silent %d min; deadline" % minutes
        if not self.power.get("cycle"):
            self._deadline_refuse("power: would cycle now (%s; %s); dry run, set \"cycle\": true in config.json "
                                  "to arm" % (reason, before), key="dry run")
            return False
        self._deadline_refused = None
        self._cycle(reason, before, kind="deadline")
        return True

    def _cycle(self, reason, before, retry=False, unpowered=False, kind="fast"):
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
        deadline = kind == "deadline"
        if deadline:                        # 0.13.0: its own count and line form, never the cap's (decision 3)
            self._deadline_times.append(now)
            self._seeded_deadlines.add(now)
        else:
            self._cycle_times.append(now)
            self._seeded_cycles.add(now)    # so a later seed of the line written below does not count it twice
        self.power_gap_until = now + float(self.power["settle_minutes"]) * 60
        self._gap_streak = 0
        self.episode_failed_restarts = 0
        self._power_logged = False
        self._boot_check = None
        try:
            self.plug.cycle(int(self.power["off_seconds"]), sleep=self._sleep)
            self.power_gap_until = self._clock() + float(self.power["settle_minutes"]) * 60
            self._events.write("power: %s #%d in 24 h: off %d s, on (%s; %s)"
                               % ("deadline cycle" if deadline else "cycled",
                                  self.deadline_cycles_today() if deadline else self.cycles_today(),
                                  int(self.power["off_seconds"]), reason, before))
        except Exception as e:
            self._events.write("power: cycle failed: %s (%s; %s)" % (e, reason, before))
            return
        minutes = int(self.power.get("boot_check_minutes", 0) or 0)
        if minutes > 0:
            self._boot_check = {"at": self._clock() + minutes * 60,
                                "cycle": self.deadline_cycles_today() if deadline else self.cycles_today(),
                                "retry": retry, "confirmed": False, "http_ok": False, "unpowered": unpowered}

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
            self._events.write("power: %s (%s); %s, leaving it to the ladder"
                               % (why, reading, "that was the one cycle for a box drawing no power"
                                  if bc.get("unpowered") else "already cycled again once"))
            return
        cap = int(self.power["max_cycles_per_day"])
        if self.cycles_today() >= cap:
            self._events.write("power: %s (%s), but %d cycles in 24 h is the cap; not cycling"
                               % (why, reading, cap))
            return
        self._cycle(why, reading, retry=True)
