"""Miner restarts over the last days, each with what the logs say caused it (0.11.0).

The question this answers is the one the 72-hour runs at 525 MHz kept asking: is the miner restarting
itself, and how often? A restart is any fall in cgminer's uptime counter in `log.csv`, so one is found
even when gbox was not sampling at the moment it happened; it is also caught when the implied start
time (`time - elapsed`) moved forward across an outage or a sampling gap, which is how a restart
behind a long gap shows (the same two tests as `gbox.markers`).

Each restart gets one cause, from the `events.log` lines between the last good sample before it and
the first good one after, and from the plug's wall reading while the miner was down. First match wins:

1. `you`: something the owner did or planned: a switch or a cycle from the page, the command line or the
   power schedule (`power: switched|cycled by you|hand|the schedule`), or a button on the page
   (`dashboard: soft restart sent|clock set to|...|power`). A planned night off is not the miner's fault.
2. `power`: power lost ahead of the plug (the plug itself stopped answering too).
3. `hung` or `power`: the watchdog acted (a soft restart, a power cycle). If the plug read under
   `POWER_LOST_WATTS` before the first power action, the miner had lost its power and the watchdog only
   brought it back: `power`. Otherwise the miner was powered and stuck: `hung`.
4. `unseen`: gbox itself started in the window, or no failed sample at all over a gap longer than
   `markers.UNSAMPLED_SECONDS`: nobody was watching, so neither the moment nor the cause is known.
5. `power` or `own`: no action at all. Under `POWER_LOST_WATTS` while down, the controller had no
   power; at or over it, it was powered and the mining process restarted by itself.
6. `unmeasured`: none of the above and no wall reading while it was down.

Why 8 W: on the SC-BOX, the two power losses on record (2026-09-25 17:46 and 23:26) read 2.6 and 4.4 W
while down, and the five restarts and hangs of its own read 14 to 27 W (the controller alive, the
hashboard idle). Seven events: a start, not proof, so the page shows the watts behind each call.

Only the readings before gbox's first power action count toward that test, because a power cycle's
own off period reads 0 W and would make every hang look like a power loss.

Counts cover the last `COUNT_DAYS`; the runs and the restarts list cover `WINDOW_DAYS`, the extra day
there for context (Mark, 2026-10-01: "counts over rolling 7 days, 8th day is for overlap context").

The log's stamps are naive local time, so the rows are read in file order, never sorted: at the fall-back
of daylight saving the hour 01:00 to 02:00 is written twice, and a sort by stamp would interleave the two
passes into a restart every other row (found in review, 2026-10-01). A stamp that steps forward with the
miner's uptime only a poll further on is the PC's clock, not a gap: a restart across a gap needs its
implied start after the last good sample, and the time hashing trusts the uptime's own step there.
"""
import csv
import datetime
import math
import re
from pathlib import Path

from . import markers

WINDOW_DAYS = 8
COUNT_DAYS = 7
POWER_LOST_WATTS = 8.0
STAMP = "%Y-%m-%d %H:%M:%S"
EVENT_SLACK_SECONDS = 5           # an event line stamped this close outside the down window still belongs to it
CAUSES = ("own", "hung", "power", "you", "unseen", "unmeasured")
MINERS = ("own", "hung")          # the miner's own doing; the rest are not, or not known

_STAMPED = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$")
_YOU = re.compile(r"^(?:power: (?:switched (?:on|off)|cycled) by (?:you|hand|the schedule)|hold: started by the schedule|"
                  r"dashboard: (?:soft restart sent|clock set to|fan target set to|switched to preset|power))")
_UNREACHABLE = re.compile(r"^power: plug unreachable")
_GBOX = re.compile(r"^(?:power: cycled|power: switched|watchdog: restart)")
_POWER_ACTION = re.compile(r"^power: (?:cycled|switched)")
_SERVICE = re.compile(r"^service: started")


def _stamp(t):
    return t.strftime(STAMP)


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None     # a "nan" cell would otherwise break int() on every request


def read_rows(path, now=None):
    """The rows `report` needs, with only the columns it reads, from a day before the window on.

    Not `series.read_rows`: that one parses every chip of every row, about 180 MB and many seconds
    for a month of log, and this record needs six columns of the last nine days.
    """
    path = Path(path)
    if not path.is_file():
        return []
    since = _stamp((now or datetime.datetime.now()) - datetime.timedelta(days=WINDOW_DAYS + 1))
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            stamp = r.get("time") or ""
            if stamp < since:
                continue                # the stamp sorts as text, so old rows are skipped before any parsing
            try:
                t = datetime.datetime.strptime(stamp, STAMP)
            except ValueError:
                continue
            elapsed = _num(r.get("elapsed"))
            out.append({"t": t, "ok": r.get("http") == "ok", "elapsed": None if elapsed is None else int(elapsed),
                        "mhs_20s": _num(r.get("mhs_20s")), "clock": _num(r.get("clock")), "watts": _num(r.get("watts"))})
    return out


def _events(lines):
    out = []
    for line in lines:
        m = _STAMPED.match((line or "").rstrip("\r\n"))
        if not m:
            continue
        try:
            out.append((datetime.datetime.strptime(m.group(1), STAMP), m.group(2)))
        except ValueError:
            continue
    return out


def _cause(down, evts, unwatched=False):
    """(cause, watts_min, watts_max) for one restart; `down` is the failed rows between the two good samples,
    `unwatched` true when the two good samples are further apart than a sampling gap with no failed one between."""
    texts = [x for _, x in evts]
    first_power = min((t for t, x in evts if _POWER_ACTION.match(x)), default=None)
    watts = [r["watts"] for r in down if r.get("watts") is not None and (first_power is None or r["t"] < first_power)]
    lo, hi = (min(watts), max(watts)) if watts else (None, None)
    # yours only when your line came before the watchdog's first action: a scheduled night off that lands
    # in the middle of a hang the watchdog was already working must not hide the hang (review, 2026-10-01)
    first_you = min((t for t, x in evts if _YOU.match(x)), default=None)
    first_dog = min((t for t, x in evts if _GBOX.match(x) and not _YOU.match(x)), default=None)
    if first_you is not None and (first_dog is None or first_you <= first_dog):
        return "you", lo, hi
    if any(_UNREACHABLE.match(x) for x in texts):
        return "power", lo, hi
    if first_dog is not None:
        return ("power" if lo is not None and lo < POWER_LOST_WATTS else "hung"), lo, hi
    if unwatched or any(_SERVICE.match(x) for x in texts):
        return "unseen", lo, hi
    if lo is not None:
        return ("power" if lo < POWER_LOST_WATTS else "own"), lo, hi
    return "unmeasured", lo, hi


def _hashing(r):
    return bool(r["ok"] and r.get("mhs_20s") and r["mhs_20s"] > 0)


def report(rows, events=(), now=None):
    """The uptime record for `/api/uptime`: runs, restarts newest first, counts, time hashing, current and longest run.

    `rows` are `read_rows` rows (or `series.read_rows` rows) in FILE order, never sorted (see the module
    docstring: daylight saving); `events` are raw `events.log` lines.
    """
    now = now or datetime.datetime.now()
    window_from = now - datetime.timedelta(days=WINDOW_DAYS)
    count_from = now - datetime.timedelta(days=COUNT_DAYS)
    rows = [r for r in rows if r["t"] <= now]
    covers_from = min((r["t"] for r in rows), default=None)
    evts = _events(events)
    slack = datetime.timedelta(seconds=EVENT_SLACK_SECONDS)
    unsampled = datetime.timedelta(seconds=markers.UNSAMPLED_SECONDS)
    restart_slack = markers.RESTART_SLACK_SECONDS

    restarts, runs = [], []
    last, down, run_start = None, [], None
    for r in rows:
        if not r["ok"] or r.get("elapsed") is None:
            if last is not None:
                down.append(r)
            continue
        start = r["t"] - datetime.timedelta(seconds=r["elapsed"])
        if last is None:
            run_start = start
        else:
            prev_start = last["t"] - datetime.timedelta(seconds=last["elapsed"])
            fell = r["elapsed"] + restart_slack < last["elapsed"]
            # across failed polls or a sampling gap, and starting after the last good sample: a restart in the
            # gap. Not when nothing failed and the uptime moved on by no more than a sampling gap while the
            # stamps jumped: that is the PC's clock (spring-forward), whatever the run's age (review, 2026-10-01).
            step = r["elapsed"] - last["elapsed"]
            clock_jump = not down and 0 <= step <= markers.UNSAMPLED_SECONDS
            moved = ((start - prev_start).total_seconds() > restart_slack and (bool(down) or r["t"] - last["t"] > unsampled)
                     and (start - last["t"]).total_seconds() > -restart_slack and not clock_jump)
            if fell or moved:
                inside = [(t, x) for t, x in evts if last["t"] - slack <= t <= r["t"] + slack]
                cause, lo, hi = _cause(down, inside, not down and r["t"] - last["t"] > unsampled)
                restarts.append({"t": _stamp(start), "back": _stamp(r["t"]), "down_from": _stamp(last["t"]),
                                 "up_before_h": round(last["elapsed"] / 3600.0, 2), "clock": last.get("clock"),
                                 "cause": cause, "watts_min": lo, "watts_max": hi,
                                 "plug_unreachable": any(_UNREACHABLE.match(x) for _, x in inside),
                                 "events": [x for _, x in inside if _YOU.match(x) or _UNREACHABLE.match(x)
                                            or _GBOX.match(x) or _SERVICE.match(x)][:6],
                                 "counted": start >= count_from, "_start": start})
                runs.append({"_start": run_start, "_end": last["t"], "ongoing": False})
                run_start = start
        last, down = r, []
    if last is not None:
        ongoing = not down and now - last["t"] <= unsampled
        runs.append({"_start": run_start, "_end": now if ongoing else last["t"], "ongoing": ongoing,
                     "_clock": last.get("clock")})

    runs = [x for x in runs if x["_end"] >= window_from]
    for x in runs:
        x["start"], x["end"] = _stamp(x["_start"]), _stamp(x["_end"])
        x["hours"] = round((x["_end"] - x["_start"]).total_seconds() / 3600.0, 2)
    restarts = [x for x in restarts if x["_start"] >= window_from]

    counts = {c: 0 for c in CAUSES}
    for x in restarts:
        if x["counted"]:
            counts[x["cause"]] += 1
    counts["total"] = sum(counts[c] for c in CAUSES)
    counts["miner"] = sum(counts[c] for c in MINERS)

    current = None
    if runs and runs[-1]["ongoing"]:
        c = runs[-1]
        current = {"start": c["start"], "hours": c["hours"], "clock": c.get("_clock")}
    longest = max(runs, key=lambda x: x["hours"]) if runs else None
    if longest is not None:
        longest = {k: longest[k] for k in ("start", "end", "hours", "ongoing")}

    out_runs = [{k: x[k] for k in ("start", "end", "hours", "ongoing")} for x in runs]
    out_restarts = [{k: v for k, v in x.items() if not k.startswith("_")} for x in reversed(restarts)]
    # covers_from: the oldest row read. Later than `from` means the log holds less than the window (a young
    # install, or a rotation with a log.keep_hours under 192 saved in config.json); the page says so.
    return {"now": _stamp(now), "from": _stamp(window_from), "count_from": _stamp(count_from),
            "covers_from": _stamp(covers_from) if covers_from else None,
            "days": WINDOW_DAYS, "count_days": COUNT_DAYS, "power_lost_watts": POWER_LOST_WATTS,
            "runs": out_runs, "restarts": out_restarts, "counts": counts,
            "hashing": hashing_time(rows, count_from, now), "current": current, "longest": longest}


def hashing_time(rows, since, now):
    """Seconds hashing, down and not sampled between `since` and `now`, and the share of sampled time hashing.

    Between two samples no further apart than `markers.UNSAMPLED_SECONDS` the earlier one's state holds.
    A longer gap next to a failed sample is an outage gbox was watching (its polls slow down while the
    watchdog works), so it is down. A longer gap between two good samples is gbox not running: not sampled,
    counted neither way. Hashing means a good sample with a hashrate over zero.

    Between two good samples of one run the miner's own uptime step is the truth when the PC's stamps
    disagree with it (a daylight-saving change or a clock correction); a step backward counts nothing.
    """
    up = dn = unk = 0.0
    seq = [r for r in rows if since <= r["t"] <= now]
    if seq:
        seq = seq + [{"t": now, "ok": seq[-1]["ok"], "mhs_20s": seq[-1].get("mhs_20s")}]
    for a, b in zip(seq, seq[1:]):
        dt = (b["t"] - a["t"]).total_seconds()
        ea, eb = a.get("elapsed"), b.get("elapsed")
        if (a["ok"] and b["ok"] and ea is not None and eb is not None and 0 <= eb - ea <= markers.UNSAMPLED_SECONDS
                and abs(dt - (eb - ea)) > markers.RESTART_SLACK_SECONDS):
            dt = eb - ea                # the stamps jumped, the uptime did not: a frozen uptime is not this
        if dt < 0:
            continue
        if dt <= markers.UNSAMPLED_SECONDS:
            if _hashing(a):
                up += dt
            else:
                dn += dt
        elif not a["ok"] or not b["ok"]:
            dn += dt
        else:
            unk += dt
    pct = 100.0 * up / (up + dn) if up + dn > 0 else None
    return {"up_s": round(up), "down_s": round(dn), "unsampled_s": round(unk), "pct": pct}
