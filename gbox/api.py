"""One serialized session with the miner, plus the parsers for what it returns.

Two firmware quirks shape this module (details in docs/firmware-api.md):

- The token check has a race. With several requests in flight it answers 401
  to roughly one in 200. So every request goes through one lock, a 401 is
  retried before it is believed, and only then is the session re-logged-in.
- Request bursts crash the web backend. The lock also means a caller cannot
  accidentally fan out.

Nothing here logs a URL: the login URL carries the encrypted password.
"""
import collections
import datetime
import hashlib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import aes


class MinerError(Exception):
    """The miner could not be reached or answered with an error."""


class HttpStatusError(MinerError):
    """The miner answered, with an HTTP error status other than 401 (a 404 or 500 on an endpoint this
    model lacks). Distinct from a miner that did not answer at all."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class AuthError(MinerError):
    """Login rejected, or 401 persisted after retries and re-login."""


class NoCredentials(AuthError):
    """A request needs a token and the session has neither a token nor a password."""


# ---------------------------------------------------------------- parsers

def kv(text, key):
    """Value of `[key] => value` in cgminer-style text, or None."""
    m = re.search(r"\[" + re.escape(key) + r"\] => ([^\n]*)", text)
    return m.group(1).strip() if m else None


_MINERINFO_FIELDS = {
    "elapsed": "Device Elapsed", "mhs_av": "MHS av", "mhs_20s": "MHS 20s",
    "accepted": "Accepted", "rejected": "Rejected", "hw_errors": "Hardware Errors",
    "hw_pct": "Device Hardware%", "clock": "clock", "fan0": "fan0", "fan1": "fan1",
    "chip_temp": "tstemp-0", "chip_temp1": "tstemp-1", "board_temp": "tstemp-2",
    "rebootcnt": "rebootcnt", "overheat": "overheat",
}


def _num(s):
    if s is None or s == "":
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() and "." not in s else f


def parse_minerinfo(text):
    """The `/dbg/minerinfo` totals the tools use, as numbers (None when absent). One board: that board's
    fields, unchanged from every earlier release. Several: `board_totals` over every `[PGAn]` block."""
    return board_totals(parse_minerinfo_boards(text))


_PGA_RE = re.compile(r"^\[PGA(\d+)\] =>", re.M)


def _fan_list(read):
    """`read(n)` for fan0, fan1, ... until the first gap (fan0..fan7 at most)."""
    fans = []
    for n in range(8):
        v = read(n)
        if v is None:
            break
        fans.append(v)
    return fans


def _board_from_kv(block, unit_text, index):
    """One per-board record from a `[PGAn] =>` block (or, for a firmware that writes none, the whole text).
    `voltage`/`current` are unit-level: on the text transport they appear once, in `[STATUS]`, outside any
    `[PGAn]` block, so they are read from `unit_text` (the whole response) rather than from `block`."""
    b = {name: _num(kv(block, key)) for name, key in _MINERINFO_FIELDS.items()}
    b["board"] = index
    b["nonced"] = _num(kv(block, "Nonced"))
    b["fans"] = _fan_list(lambda n: _num(kv(block, "fan%d" % n)))
    b["voltage_mv"] = _num(kv(unit_text, "voltage"))
    b["current_ma"] = _num(kv(unit_text, "current"))
    return b


def parse_minerinfo_boards(text):
    """One dict per `[PGAn] =>` block of `/dbg/minerinfo`, in order. No block: one dict from the whole text
    (a firmware that writes no PGA headers still gets today's behaviour)."""
    heads = list(_PGA_RE.finditer(text))
    if not heads:
        return [_board_from_kv(text, text, 0)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append(_board_from_kv(text[m.start():end], text, int(m.group(1))))
    return out


def parse_devs4028(text):
    """The `devs` reply on port 4028 -> the same per-board list. Strips cgminer's trailing NUL. Unlike the
    text transport, `voltage`/`current` are repeated per board here, so each is read straight off its own
    DEVS entry."""
    j = json.loads(text.rstrip("\x00"))
    out = []
    for d in j.get("DEVS", []):
        g = lambda k: _num(None if d.get(k) is None else str(d.get(k)))
        b = {name: g(key) for name, key in _MINERINFO_FIELDS.items()}
        b["board"] = int(d.get("PGA", len(out)))
        b["nonced"] = g("Nonced")
        b["fans"] = _fan_list(lambda n: g("fan%d" % n))
        b["voltage_mv"] = g("voltage")
        b["current_ma"] = g("current")
        out.append(b)
    return out


def _hottest_index(boards):
    """Index of the board with the highest `chip_temp` (ties keep the earlier board); 0 when none is known."""
    best_i, best_t = 0, None
    for i, b in enumerate(boards):
        t = b["chip_temp"]
        if t is not None and (best_t is None or t > best_t):
            best_i, best_t = i, t
    return best_i


def board_totals(boards):
    """The dict every caller of `parse_minerinfo` uses. One board: that board's fields, unchanged. Several:
    sums for hashrates, shares and errors; `hw_pct` as errors over nonces (percent) when every board's nonce
    count is known, else the mean of the boards' own percentages; `elapsed` the max; `clock` the first
    non-None; `rebootcnt` and `overheat` the max; `chip_temp`, `chip_temp1` and `board_temp` from the
    HOTTEST board (max `chip_temp`), so the watchdog, the charts and log.csv's tstemp columns mean "the
    hottest board" on a big unit and exactly what they mean today on the SC-BOX; `fan0`/`fan1` the first two
    of the unit's fans. New keys: `fans` (list), `nboards`, `hot_board` (index), `watts_dc` (mV*mA/1e6, or
    None when either is unknown)."""
    if len(boards) == 1:
        out = {name: boards[0][name] for name in _MINERINFO_FIELDS}
    else:
        out = {}
        elapsed = [b["elapsed"] for b in boards if b["elapsed"] is not None]
        out["elapsed"] = max(elapsed) if elapsed else None
        for key in ("mhs_av", "mhs_20s", "accepted", "rejected", "hw_errors"):
            vals = [b[key] for b in boards if b[key] is not None]
            out[key] = sum(vals) if vals else None
        nonced = [b["nonced"] for b in boards]
        if all(n is not None for n in nonced) and sum(nonced) > 0 and \
                all(b["hw_errors"] is not None for b in boards):
            out["hw_pct"] = 100.0 * sum(b["hw_errors"] for b in boards) / sum(nonced)
        else:
            pcts = [b["hw_pct"] for b in boards if b["hw_pct"] is not None]
            out["hw_pct"] = (sum(pcts) / len(pcts)) if pcts else None
        out["clock"] = next((b["clock"] for b in boards if b["clock"] is not None), None)
        rebootcnt = [b["rebootcnt"] for b in boards if b["rebootcnt"] is not None]
        out["rebootcnt"] = max(rebootcnt) if rebootcnt else None
        overheat = [b["overheat"] for b in boards if b["overheat"] is not None]
        out["overheat"] = max(overheat) if overheat else None
        hot = boards[_hottest_index(boards)]
        out["chip_temp"] = hot["chip_temp"]
        out["chip_temp1"] = hot["chip_temp1"]
        out["board_temp"] = hot["board_temp"]
        fans = boards[0]["fans"]
        out["fan0"] = fans[0] if len(fans) > 0 else None
        out["fan1"] = fans[1] if len(fans) > 1 else None
    out["fans"] = boards[0]["fans"] if boards else []
    out["nboards"] = len(boards)
    out["hot_board"] = _hottest_index(boards)
    v, c = boards[0]["voltage_mv"], boards[0]["current_ma"]
    out["watts_dc"] = (v * c / 1e6) if (v is not None and c is not None) else None
    return out


_CHIPTEMP_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?Chip Avgtemp (-?\d+(?:\.\d+)?)'C, MaxTemp (-?\d+(?:\.\d+)?)'C")


# The start of a run: the board's init, or the mining process's banner, which a cold boot writes before its clock
# is set. The log survives a power cycle, so what comes before the newest of these belongs to the run before.
# Anchored at the start of the message, so a pool user that happens to contain either phrase cannot move it.
_RUN_START_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?(?:C\d+: )?(?:SCBOX Init sucessed|Started intminer )")
# The mining process's banner alone: where a first read of the log stops (0.10.0). The board's init is not a
# boundary there, because the miner re-inits its board mid-run after a fault, and the lines before a re-init are
# the ones that say why.
_PROCESS_START_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?Started intminer ")


_SYSLOG_TS_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ?", re.ASCII)


# 0.10.0: where a read of the log ended, as a place in the log. `index` is the last line read and `count` the
# number of lines the log had then; `digest` is that line's SHA-256, so the cursor holds nothing of the log's text
# (the log repeats the pool user). `stamp` is the miner's time on or before that line, kept only to tell a
# truncated log's old lines from its new ones within one stretch of clock (see `_start`).
LogCursor = collections.namedtuple("LogCursor", "stamp index digest count")


def _digest(line):
    return hashlib.sha256(line.encode("utf-8", "surrogatepass")).digest()


def _whole_lines(text):
    """The log's lines, less a last line with no line end yet: the miner may be writing it as the read is made,
    and the next read has it whole."""
    lines = text.splitlines()
    if lines and text[-1] not in "\r\n":
        lines.pop()
    return lines


def _stamp(line):
    """The line's miner time as a datetime, or None when it has none or it is not a real date."""
    m = _SYSLOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _cursor_at_end(lines):
    """A LogCursor on the log's last line, or None for an empty log."""
    if not lines:
        return None
    stamp = next((s for s in map(_stamp, reversed(lines)) if s is not None), None)
    return LogCursor(stamp and stamp.strftime("%Y-%m-%d %H:%M:%S"), len(lines) - 1, _digest(lines[-1]), len(lines))


def _start(lines, cursor, first_minutes):
    """The index of the first line a read takes.

    A cursor is trusted when the log still has at least as many lines as it had and the line at the cursor is the
    same line: then the read starts just after it. That is exact for a log that is only appended to, whatever its
    clock did, and it reads a line written later in the same second as the cursor's.

    Otherwise (a first read, or the log was truncated or rewritten) the read scans backward from the end and stops
    at the newest process start (`_PROCESS_START_RE`, taken), at a stamp that goes UP while scanning backward (the far side
    of a clock set back: not taken), or at a stamp more than `first_minutes` older than the log's last one (not
    taken), whichever comes first. `first_minutes` None takes the whole log. No stamp is compared across a run
    start or a clock jump.

    After a truncation the stretch found may still hold lines the cursor already read. Only when that stretch is
    one run with no clock jump in it, and the cursor's stamp is not later than the stretch's last, are its lines
    at or before the cursor's stamp dropped. A truncation and a boot with no time between two reads can still
    duplicate or drop a few lines (docs/security-notes.md)."""
    if cursor is not None and 0 <= cursor.index < cursor.count <= len(lines) and \
            _digest(lines[cursor.index]) == cursor.digest:
        return cursor.index + 1
    if first_minutes is None:
        return 0
    start, newest, prev, bounded = len(lines), None, None, False
    for i in range(len(lines) - 1, -1, -1):
        ts = _stamp(lines[i])
        if ts is None:
            continue
        if newest is None:
            newest = ts
        if prev is not None and ts > prev:
            bounded = True
            break
        if ts < newest - datetime.timedelta(minutes=first_minutes):
            break
        start, prev = i, ts
        if _PROCESS_START_RE.match(lines[i]):
            bounded = True
            break
    if cursor is None or bounded or cursor.stamp is None or newest is None:
        return start
    seen = datetime.datetime.strptime(cursor.stamp, "%Y-%m-%d %H:%M:%S")
    if seen > newest:
        return start
    while start < len(lines):
        ts = _stamp(lines[start])
        if ts is not None and ts > seen:
            break
        start += 1
    return start


# classify_syslog (0.9.0). The miner truncates its own log: 3.8 MB at 18:25 on 2026-09-20 and 36 KB two hours
# later, with no restart, so an incident's lines are gone by the time anyone asks. These tables turn the read the
# service already makes into labels and counts. Every pattern is matched at the start of the message, on its first
# _SYSLOG_MSG_CHARS characters only, and none nests a quantifier: the input is megabytes of text from a device.
_SYSLOG_MSG_CHARS = 200
_SYSLOG_LABEL_RES = [(label, re.compile(pattern)) for label, pattern in (
    ("process_started", r"Started intminer "),
    ("init_succeeded", r"(?:C\d+: )?SCBOX Init sucessed"),
    ("init_failed", r"(?:C\d+: )?Init failed \d+ Times"),
    ("chip_write_failed", r"(?:C\d+: )?Write Chip\d+ Reg \d+ Failed"),
    ("reg_read_error", r"(?:C\d+: )?Read reg \d+ error"),
    ("bist_error", r"(?:C\d+: )?BistStart err"),
    ("sendjob_reinit", r"(?:C\d+: )?!!!SEND JOB FAILD"),
    ("readnonce_reinit", r"(?:C\d+: )?Read Nonce Faild"),
    ("addressing_failed", r"(?:C\d+: )?Auto addressing failed"),
    ("set_clock_failed", r"(?:C\d+: )?Set clk to \d+MHz Failed"),
    ("clock_nan", r"(?:C\d+: )?ICT\d+ set Clock\(req [\d.]+MHz, actual nan"),     # before the routine clock ramp below
    ("tsensor_failed", r"(?:C\d+: )?(?:Set TSENSOR_MODE|Read TV_ACCESS MAX) Failed"),
    ("pool_not_responding", r"Pool \d+ \S+ not responding"),
    ("stratum_interrupted", r"Stratum connection to pool \d+ interrupted"),
    ("fatal_exit", r"INCS \d+ failure, exiting"),
    ("thread_shutdown", r"(?:C\d+: )?gsb\d+_thread_shutdown"),
    ("invalid_nonce", r"INCS \d+: invalid nonce"),
    ("cpb_idle", r"(?:C\d+: )?WatchDog Exit for CPB Idle"),
    ("settings_applied", r"gsb\d+ dev\d+: set device vfff"),
)]
SYSLOG_LABELS = tuple(label for label, _ in _SYSLOG_LABEL_RES)
# 0.10.0: what a read says about the pool, for the watchdog (`classify_syslog`'s `signals`). "pool": the pool is
# not answering. "fault": a line that points at the miner itself. "start": the mining process started. "probing":
# it is looking for a pool (routine in minerlog.csv, evidence here). "accepted": a share line. The last two are
# never an episode's end: on 2026-09-22 two "Accepted" lines were logged eight minutes into a real outage. "shares"
# (0.10.1, first read only, see classify_syslog): share lines spanning a minute after a pool line, which is one.
POOL_LABELS = ("pool_not_responding", "stratum_interrupted")
FAULT_LABELS = ("init_failed", "chip_write_failed", "reg_read_error", "bist_error", "sendjob_reinit",
                "readnonce_reinit", "addressing_failed", "set_clock_failed", "clock_nan", "tsensor_failed",
                "fatal_exit", "thread_shutdown", "cpb_idle")
# 0.10.1: on a first read, "shares" follows a pool line when share lines after it span this long by the log's own
# stamps: the pool came back before the service started. The same span the watchdog asks of the counter
# (watchdog.SHARES_BACK_SECONDS; a test holds them equal), so the measured outage's two shares 30 s apart stay held.
LOG_SHARES_BACK_SECONDS = 60
# A step between two share lines that goes back, or forward by more than this, restarts the span: a clock set back
# to 2007 and then forward again is not years of shares. A pool that is back gives one every 10-40 s (2026-09-22).
LOG_SHARES_MAX_STEP_SECONDS = 300
_PROBING_RE = re.compile(r"Probing for an alive pool")
_ACCEPTED_RE = re.compile(r"Accepted ")
# What a healthy miner writes all day, and the banner of a start. Ignored, not counted.
_SYSLOG_ROUTINE_RE = re.compile(
    r"(?:C\d+: )?(?:Chip Avgtemp |Work restart!|scanhash workid |Work\(\d+\) Hash Scan finished|Auto DTFS now check"
    r"|ICT\d+ set Clock\(|SCBOX device init\.\.\.|SCBOX scan time is |gsb\d+_reinit|gsb\d+_thread_init|tvout:|nonce#\()"
    r"|SCBOX C\d+: =======>|Accepted |Stratum from pool \d+ detected new block|(?:====>)?Pool \d+ stratum difficulty "
    r"|Pool \d+ \S+ (?:alive|user )|arg |Loaded configuration file |SCBox driver parse succeed|Goldshell-\S+ +Miner detected"
    r"|Probing for an alive pool|API running in ")


def _signal(label, msg):
    if label in POOL_LABELS:
        return "pool"
    if label in FAULT_LABELS:
        return "fault"
    if label == "process_started":
        return "start"
    if label is None and _PROBING_RE.match(msg):
        return "probing"
    if label is None and _ACCEPTED_RE.match(msg):
        return "accepted"
    return None


def classify_syslog(text, cursor=None, first_minutes=None, signals=None, signal_minutes=None):
    """The non-routine lines of `/dbg/minersyslog` as `(rows, cursor)`: `rows` is a list of
    `(label, count, first_miner_timestamp, last_miner_timestamp)` in the order each label first appeared, and
    `cursor` is a `LogCursor` on the log's last whole line, for the next read (None for an empty log).

    Lines are taken by their place in the log (`_start`, 0.10.0): after the caller's `cursor` when it still
    fits, else a first read looking back `first_minutes` (None: the whole log). The miner's clock is never used
    to order lines: it restarts at 2007 on a boot with no time server and can be set back.

    Nothing of a line's text is returned, ever: the labels are this module's own, the counts are ints, and the
    timestamps are ASCII digits that parse as a real date. The log repeats the pool user, and a mask would be a
    blacklist that fails open on a line shape nobody anticipated; a line no pattern knows is counted under
    "other" and its text is dropped. A line without a real timestamp is skipped. A line stamped in the future is
    counted with its stamp and hides nothing: the cursor is a place, not a time.

    `signals`, a list, receives the read's pool evidence in log order (`_signal`: "pool", "fault", "start",
    "probing", "accepted"), a run of the same one kept once, so it stays short on a log of shares. On a first
    read the signals look back `signal_minutes` (when wider than `first_minutes`) while the rows keep to
    `first_minutes`: a service started after the log went quiet in an outage must still see the pool lines
    (0.10.0 review, M2). Any cursor, fitting or not (a truncation), gives both the same start.

    Also on a first read only, "shares" follows a pool line once share lines after it span
    LOG_SHARES_BACK_SECONDS of log time (0.10.1): that outage ended before the service started. After a cursor
    the watchdog's counter decides that instead. A step between share stamps that goes back, or forward by more
    than LOG_SHARES_MAX_STEP_SECONDS, restarts the span.
    """
    lines = _whole_lines(text)
    found = {}
    start = _start(lines, cursor, first_minutes)
    first = start
    if signals is not None and cursor is None and first_minutes is not None and signal_minutes is not None \
            and signal_minutes > first_minutes:
        first = min(start, _start(lines, cursor, signal_minutes))
    pool_pending, shares_since, shares_last = False, None, None
    for i in range(first, len(lines)):
        line = lines[i]
        m = _SYSLOG_TS_RE.match(line)
        if not m or _stamp(line) is None:
            continue
        ts = m.group(1)
        msg = line[m.end():m.end() + _SYSLOG_MSG_CHARS]
        label = None
        for name, pattern in _SYSLOG_LABEL_RES:
            if pattern.match(msg):
                label = name
                break
        if signals is not None:
            sig = _signal(label, msg)
            if sig is not None and (not signals or signals[-1] != sig):
                signals.append(sig)
            if cursor is None and sig in ("pool", "fault", "start"):
                pool_pending, shares_since = sig == "pool", None
            elif cursor is None and sig == "accepted" and pool_pending:
                t = _stamp(line)
                step = None if shares_since is None else (t - shares_last).total_seconds()
                if step is None or not 0 <= step <= LOG_SHARES_MAX_STEP_SECONDS:
                    shares_since = shares_last = t                # the first share, or the far side of a clock jump
                elif (t - shares_since).total_seconds() >= LOG_SHARES_BACK_SECONDS:
                    signals.append("shares")
                    pool_pending, shares_since = False, None
                else:
                    shares_last = t
        if i < start:
            continue                                              # evidence only; minerlog.csv keeps its window
        if label is None:
            if not msg.strip() or _SYSLOG_ROUTINE_RE.match(msg):
                continue
            label = "other"
        if label in found:
            found[label][0] += 1
            found[label][2] = ts                                  # the last in log order, not the largest
        else:
            found[label] = [1, ts, ts]
    return [(label, v[0], v[1], v[2]) for label, v in found.items()], _cursor_at_end(lines)


def parse_chiptemps(text, cursor=None, first_minutes=None):
    """The `/dbg/minersyslog` temperature lines as `(readings, cursor)`: `readings` is a list of
    (miner_timestamp, chip_avg, chip_max) tuples in log order, `cursor` a `LogCursor` for the next read.

    One line every 5 s: ` [2026-09-15 07:36:21] C0: Chip Avgtemp 69.000000'C, MaxTemp 79.000000'C`.
    Lines are taken by their place in the log, as in `classify_syslog`. Only the current run counts: lines
    before the newest run start (`_RUN_START_RE`) are the run before a power cycle, which the log survives.
    Nothing but numbers and timestamps leaves this function: the log repeats the pool user, so its text is
    never stored or logged by anything that calls it.
    """
    lines = _whole_lines(text)
    run = 0
    for i in range(len(lines) - 1, -1, -1):
        if _RUN_START_RE.match(lines[i]):
            run = i + 1
            break
    start = _start(lines, cursor, first_minutes)
    out = []
    for line in lines[max(run, start):]:
        m = _CHIPTEMP_RE.match(line)
        if not m:
            continue
        out.append((m.group(1), float(m.group(2)), float(m.group(3))))
    return out, _cursor_at_end(lines)


def parse_icinfo(text):
    """`/dbg/icinfo` -> list of boards, each a list of {chip, good, bad}."""
    body = json.loads(json.loads(text)["body"])
    return [[{"chip": c["chipindex"], "good": c["perf"], "bad": c["hwerr"]} for c in board]
            for board in body["drawdata"]]


def format_chips(boards):
    """Every chip's cumulative counts for the log's `chips` column: `board.chip:good/bad` joined by `;`,
    in board then chip order. Board-aware from the first row so a multi-board unit needs no second format."""
    return ";".join("%d.%d:%d/%d" % (b, c["chip"], c["good"], c["bad"])
                    for b, board in enumerate(boards) for c in board)


_CHIPS_RE = re.compile(r"^(\d+\.\d+):(\d+)/(\d+)$")


def parse_chips(text):
    """The `chips` column back into {"board.chip": (good, bad)}; junk entries are skipped, "" is {}."""
    out = {}
    for part in (text or "").split(";"):
        m = _CHIPS_RE.match(part.strip())
        if m:
            out[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return out


def chip_health(chip, best_good):
    """One of ok, weak, failing, judged relative to the best chip on the board."""
    total = chip["good"] + chip["bad"]
    bad_pct = 100.0 * chip["bad"] / total if total else 0.0
    if chip["good"] < 0.3 * best_good or bad_pct > 30:
        return "failing"
    if chip["good"] < 0.7 * best_good or chip["bad"] > 40:
        return "weak"
    return "ok"


def weak_chips(board):
    """Chips that are weak or failing, judged relative to the best chip on the board."""
    if not board:
        return []
    best = max(c["good"] for c in board)
    return [c for c in board if chip_health(c, best) != "ok"]


# The power plan string in every dialect seen so far (docs/firmware-api.md, "Power plan dialects"):
#   box       575 MHz 0.41 V 90 RPM 90 RPM             the SC-BOX, read from the unit
#   mv_pv     625 MHz 9100 V 40 RPM 40 RPM PV 9400     the SC Lite (fw 2.2.0), from the other developer's notes
#   float_pv  750 MHz 0.41 V 50 RPM 50 RPM PV 9400     the documented "float-V / optional-PV" form of the HS Box
_PLAN_RE = re.compile(r"^\s*(\d+)\s*MHz\s+([\d.]+)\s*V\s+(\d+)\s*RPM\s+(\d+)\s*RPM(?:\s+PV\s+(\d+))?\s*$")


def parse_plan(plan):
    """A power plan string into its parts, in whichever dialect the firmware wrote it.

    Returns {mhz, volts, fan_a, fan_b, pv, volts_text, dialect}. `volts` is the number as written (the SC Lite's
    9100 stays 9100.0: nobody has confirmed it is millivolts, and nothing here needs to know); `volts_text` is
    the token verbatim so `format_plan(**parse_plan(s))` gives `s` back; `pv` is None without a PV term.
    """
    m = _PLAN_RE.match(plan if isinstance(plan, str) else "")
    if not m:
        raise ValueError("not a power plan string: %r" % (plan,))
    volts_text = m.group(2)
    try:
        volts = float(volts_text)
    except ValueError:
        raise ValueError("not a power plan string: %r" % (plan,)) from None
    pv = int(m.group(5)) if m.group(5) is not None else None
    dialect = "box" if pv is None else ("float_pv" if "." in volts_text else "mv_pv")
    return {"mhz": int(m.group(1)), "volts": volts, "fan_a": int(m.group(3)), "fan_b": int(m.group(4)),
            "pv": pv, "volts_text": volts_text, "dialect": dialect}


def format_plan(mhz, volts, fan_a, fan_b, pv=None, volts_text=None, dialect=None):
    """The plan string back. Positional use writes the box form; `volts_text` (from parse_plan) is written
    verbatim, else volts is trimmed as the BOX writes it (0.4, not 0.40); a `pv` adds the PV term. `dialect`
    is accepted so `format_plan(**parse_plan(s))` works; the parts decide the form, not the label."""
    if volts_text is None:
        volts_text = ("%.2f" % volts).rstrip("0").rstrip(".")
    text = "%d MHz %s V %d RPM %d RPM" % (mhz, volts_text, fan_a, fan_b)
    return text + (" PV %d" % pv if pv is not None else "")


def with_mhz(plan, mhz, volts=None):
    """`plan` with only its clock changed (and its volts, when given); every other token as the firmware wrote
    it, whatever the dialect. This is what the clock control sends, so a unit never gets a plan in a form it
    did not write itself."""
    parts = parse_plan(plan)
    parts["mhz"] = mhz
    if volts is not None:
        parts["volts"], parts["volts_text"] = volts, None
    return format_plan(**parts)


def max_preset_mhz(setting):
    """Highest clock among the firmware presets; the top of the sane range."""
    best = 0
    for p in setting.get("powerplans", []):
        try:
            best = max(best, parse_plan(p.get("info"))["mhz"])
        except ValueError:
            pass
    return best


def hashrate_unit(mhs):
    """Pick a display unit for a value in MH/s. Returns (unit name, divisor)."""
    v = mhs or 0
    if v >= 1e6:
        return ("TH/s", 1000000)
    if v >= 1e3:
        return ("GH/s", 1000)
    return ("MH/s", 1)


# ---------------------------------------------------------------- session

class Miner:
    """Serialized HTTP session with one miner.

    Credentials are any one of: a password, its encrypted hex form, or a
    ready token. Without any, requests raise NoCredentials.
    """

    RETRY_401 = 2          # retries of a single request before the 401 is believed
    RETRY_DELAY = 0.7      # seconds, grows linearly

    def __init__(self, host, password=None, password_hex=None, token=None, timeout=15):
        if not host:
            raise ValueError("miner host is required")
        self.host = host
        self.base = host if host.startswith("http") else "http://" + host
        self.timeout = timeout
        self._password_hex = aes.encrypt_password(password) if password else password_hex
        self._token = token
        self._lock = threading.Lock()
        self.request_count = 0
        self.unauthorized_count = 0

    # -- credentials

    @property
    def has_token(self):
        return bool(self._token)

    @property
    def can_login(self):
        return bool(self._password_hex)

    @property
    def has_credentials(self):
        return self.has_token or self.can_login

    def set_token(self, token):
        if not isinstance(token, str) or token.count(".") != 2:
            raise ValueError("not a JWT")
        self._token = token

    def forget_token(self):
        self._token = None

    def login(self):
        """Exchange the password for a token. Serialized like every other request."""
        with self._lock:
            return self._login_locked()

    def verify_password_hex(self, password_hex):
        """True when the miner accepts this encrypted password. AuthError when it rejects it (or the
        input is not one), MinerError when it does not answer. The session's own token is untouched.

        Used by the service for the power buttons: the page proves the password the way the clock
        button does, by a login, but the service is the one holding the plug.
        """
        try:
            raw = bytes.fromhex(password_hex) if isinstance(password_hex, str) else b""
        except ValueError:
            raw = b""
        if not raw or len(raw) % 16:
            raise AuthError("password check: not an encrypted password")
        with self._lock:
            self._login_locked(password_hex, store=False)
        return True

    def _login_locked(self, password_hex=None, store=True):
        hexpw = password_hex or self._password_hex
        if not hexpw:
            raise NoCredentials("no password to log in with")
        url = "%s/user/login?%s" % (self.base, urllib.parse.urlencode(
            {"username": "admin", "password": hexpw, "cipher": "true"}))
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise AuthError("login failed: HTTP %d" % e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise MinerError("login: %s" % _describe(e)) from None
        tok = body.get("JWT Token") if isinstance(body, dict) else None
        if not isinstance(tok, str) or tok.count(".") != 2:
            raise AuthError("login rejected (wrong password?)")
        if store:
            self._token = tok
        return tok

    # -- raw requests

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, body=None):
        return self.request("PUT", path, body)

    def request(self, method, path, body=None):
        """One request, serialized, with 401 retry and one re-login. Returns the body text."""
        with self._lock:
            if not self._token:
                self._login_locked()
            relogged = False
            attempt = 0
            while True:
                status, text = self._send_locked(method, path, body)
                if status != 401:
                    return text
                self.unauthorized_count += 1
                if attempt < self.RETRY_401:
                    attempt += 1
                    time.sleep(self.RETRY_DELAY * attempt)
                    continue
                if self.can_login and not relogged:
                    relogged = True
                    attempt = 0
                    self._login_locked()
                    continue
                self._token = None
                raise AuthError("miner rejected the session token")

    def _send_locked(self, method, path, body):
        self.request_count += 1
        data = None
        headers = {"Authorization": "Bearer " + self._token}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request("%s/%s" % (self.base, path.lstrip("/")), data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return 401, ""
            raise HttpStatusError("%s %s: HTTP %d" % (method, path, e.code), e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise MinerError("%s %s: %s" % (method, path, _describe(e))) from None

    # -- typed reads

    def status(self):
        return json.loads(self.get("mcb/status"))

    def setting(self):
        return json.loads(self.get("mcb/setting"))

    def minerinfo(self):
        return parse_minerinfo(self.get("dbg/minerinfo"))

    def minerinfo_boards(self):
        return parse_minerinfo_boards(self.get("dbg/minerinfo"))

    def devs4028(self, port=4028):
        """The `{"command":"devs"}` reply on cgminer-style port 4028: no token, no web backend, no token
        race. Serialized under the same lock as every other request (one thing at a time is the simplest
        rule to keep)."""
        host = urllib.parse.urlparse(self.base).hostname
        with self._lock:
            try:
                with socket.create_connection((host, port), timeout=self.timeout) as s:
                    s.sendall(b'{"command":"devs"}')
                    try:
                        s.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    chunks = []
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                raise MinerError("devs 4028: %s" % _describe(e)) from None
        return parse_devs4028(b"".join(chunks).decode("utf-8", "replace"))

    def boards(self):
        return parse_icinfo(self.get("dbg/icinfo"))

    def history(self):
        return json.loads(self.get("cpb/hshistory"))

    def syslog(self):
        """The cgminer log as text (about 2 MB; the firmware truncates it near 3 MB). Never persist it."""
        return self.get("dbg/minersyslog")

    # -- writes

    def set_plan(self, mhz, volts=None):
        """Set the clock live via the manual power plan. Returns the plan string applied."""
        st = self.setting()
        current = st.get("manualPowerplan")
        try:
            parse_plan(current)
        except ValueError:
            current = st["powerplans"][0]["info"]
            parse_plan(current)
        top = max_preset_mhz(st) or 725
        if mhz % 25 or not 300 <= mhz <= top:
            raise ValueError("clock must be a multiple of 25 between 300 and %d MHz" % top)
        st["manual"] = True
        st["manualPowerplan"] = with_mhz(current, mhz, volts)     # the unit's own dialect, only the clock changed
        self.put("mcb/setting", st)
        return self.setting().get("manualPowerplan")

    def set_fan_target(self, temp):
        st = self.setting()
        lo, hi = (st.get("temp_targets") or [65, 75])[:2]
        if not lo <= temp <= hi:
            raise ValueError("fan target must be between %g and %g C on this firmware" % (lo, hi))
        st["temp_target"] = temp
        self.put("mcb/setting", st)
        return self.setting().get("temp_target")

    def restart(self):
        self.put("mcb/restart")


def _describe(exc):
    """Error text that never includes a URL."""
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return str(reason)
    return "%s: %s" % (type(exc).__name__, exc)
