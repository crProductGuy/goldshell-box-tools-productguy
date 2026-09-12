"""Clock trials: the logger's log.csv cut into segments per clock and fan target.

A segment is a run of good samples at one clock and one fan target with no
restart (the miner's counters reset), no pool reset (accepted drops) and no
hole in the log longer than `gap_s`. Each segment is summarized from the
counter deltas between its first and last sample, never from the miner's
running totals, so clocks held on different days compare fairly. `rollup`
pools the segments of one (clock, fan target) pair into the row the
dashboard and `gbox trials` show by default.

The table half is pure functions over the CSV. `run_trial` at the bottom is
the unattended runner behind `gbox trials run`: it is the one thing here that
talks to the miner, and only to set the clock at step boundaries.
"""
import csv
import datetime
import json
import re
import time
from pathlib import Path

from . import api

GAP_S = 300                 # a hole longer than this ends a segment
MIN_MINUTES = 20            # shorter segments are flagged `short` and left out of rollups
_INT = ("elapsed", "hwerr", "accepted", "rebootcnt")
_FLOAT = ("clock", "mhs_20s", "tstemp0", "fan0", "fan1")
_OPT_INT = ("nonces_good", "nonces_bad", "overheat")
_CHIP_RE = re.compile(r"(\d+):(\d+)/(\d+)")


def _num(s, cast):
    try:
        return cast(float(s))
    except (TypeError, ValueError):
        return None


def read_rows(path):
    """Good samples from log.csv with numbers parsed; rows missing a needed number are dropped."""
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("http") != "ok":
                continue
            try:
                t = datetime.datetime.strptime(r.get("time") or "", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            row = {"t": t}
            for k in _INT:
                row[k] = _num(r.get(k), int)
            for k in _FLOAT:
                row[k] = _num(r.get(k), float)
            if any(row[k] is None for k in _INT + _FLOAT):
                continue
            for k in _OPT_INT:
                row[k] = _num(r.get(k), int)
            row["hwerr_pct"] = _num(r.get("hwerr_pct"), float)
            row["temp_target"] = _num(r.get("temp_target"), int)
            row["watts"] = _num(r.get("watts"), float)       # empty before the plug, without a meter, or when it did not answer
            row["chips"] = {int(c): (int(g), int(b)) for c, g, b in _CHIP_RE.findall(r.get("weak_chips") or "")}
            out.append(row)
    return out


def _breaks(prev, row, gap_s):
    return (row["clock"] != prev["clock"] or row["temp_target"] != prev["temp_target"]
            or (row["t"] - prev["t"]).total_seconds() > gap_s
            or row["elapsed"] < prev["elapsed"] or row["accepted"] < prev["accepted"])


def segments(rows, gap_s=GAP_S):
    """Cut rows (in time order) into segments. Each keeps its first and last row, sums, and per-chip ends."""
    segs = []
    seg = None
    for row in rows:
        if seg is None or _breaks(seg["last"], row, gap_s):
            seg = {"first": row, "last": row, "n": 0, "mhs": 0.0, "temp": 0.0, "fan": 0.0, "overheat": 0, "chips": {},
                   "watts": 0.0, "watts_n": 0}
            segs.append(seg)
        seg["last"] = row
        seg["n"] += 1
        seg["mhs"] += row["mhs_20s"]
        seg["temp"] += row["tstemp0"]
        seg["fan"] += (row["fan0"] + row["fan1"]) / 2.0
        seg["overheat"] += 1 if row["overheat"] else 0
        if row["watts"] is not None:                 # its own count: watts can be missing mid-segment
            seg["watts"] += row["watts"]
            seg["watts_n"] += 1
        for chip, gb in row["chips"].items():
            c = seg["chips"].setdefault(chip, {"first": gb, "first_t": row["t"]})
            c["last"], c["last_t"] = gb, row["t"]
    for s in segs:
        s["last_segment"] = s is segs[-1]
    return segs


def _hours(a, b):
    return (b - a).total_seconds() / 3600.0


def _gh_per_w(mhs, watts):
    """Hashrate per watt in GH/s per W, or None without a positive wall reading. Always GH/s so rows compare."""
    return (mhs / 1000.0) / watts if watts else None


def summarize(seg, min_minutes=MIN_MINUTES):
    """One segment as the flat dict the table shows. Raw deltas are kept for `rollup`."""
    first, last, n = seg["first"], seg["last"], seg["n"]
    hours = _hours(first["t"], last["t"])
    worst = None
    for chip, c in seg["chips"].items():
        dg, db = c["last"][0] - c["first"][0], c["last"][1] - c["first"][1]
        if dg + db <= 0:
            continue
        pct = 100.0 * db / (dg + db)
        if worst is None or pct > worst["pct"]:
            partial = c["first_t"] != first["t"] or c["last_t"] != last["t"]
            worst = {"chip": chip, "pct": pct, "bad": db, "hours": _hours(c["first_t"], c["last_t"]), "partial": partial}
    d_hw = last["hwerr"] - first["hwerr"]
    if first["nonces_good"] is not None and last["nonces_good"] is not None:
        d_nonces = last["nonces_good"] - first["nonces_good"] + d_hw
        hw_pct, hw_approx = (100.0 * d_hw / d_nonces if d_nonces > 0 else 0.0), False
    else:
        d_nonces, hw_pct, hw_approx = None, last["hwerr_pct"], True
    d_accepted = last["accepted"] - first["accepted"]
    watts = seg["watts"] / seg["watts_n"] if seg["watts_n"] else None
    mhs = seg["mhs"] / n
    return {
        "clock": int(round(first["clock"])), "fan_target": first["temp_target"],
        "start": first["t"].strftime("%Y-%m-%d %H:%M:%S"), "end": last["t"].strftime("%Y-%m-%d %H:%M:%S"),
        "minutes": hours * 60.0, "samples": n, "short": hours * 60.0 < min_minutes, "last": seg["last_segment"],
        "worst_chip": worst["chip"] if worst else None,
        "bad_pct": worst["pct"] if worst else None,
        "bad_partial": bool(worst and worst["partial"]),
        "bad_per_hour": (worst["bad"] / worst["hours"] if worst and worst["hours"] > 0 else 0.0),
        "resets": max(0, last["rebootcnt"] - first["rebootcnt"]),
        "hw_pct": hw_pct, "hw_approx": hw_approx,
        "accepted_per_hour": d_accepted / hours if hours > 0 else 0.0,
        "mhs": mhs, "chip_temp": seg["temp"] / n, "fan_rpm": seg["fan"] / n, "overheat": seg["overheat"],
        "watts": watts, "watts_n": seg["watts_n"], "gh_per_w": _gh_per_w(mhs, watts),
        # raw deltas for pooling
        "d_accepted": d_accepted, "d_hw": d_hw, "d_nonces": d_nonces,
        "d_bad": worst["bad"] if worst else 0, "bad_hours": worst["hours"] if worst else 0.0,
    }


def rollup(summaries):
    """Pool the non-short segments of each (clock, fan target) into one row; newest first."""
    groups = {}
    for s in summaries:
        if s["short"]:
            continue
        groups.setdefault((s["clock"], s["fan_target"]), []).append(s)
    out = []
    for (clock, target), segs in groups.items():
        n = sum(s["samples"] for s in segs)
        hours = sum(s["minutes"] for s in segs) / 60.0
        with_bad = [s for s in segs if s["bad_pct"] is not None]
        worst = max(with_bad, key=lambda s: s["bad_pct"]) if with_bad else None
        bad_hours = sum(s["bad_hours"] for s in segs)
        approx = any(s["hw_approx"] for s in segs)
        if approx:
            hw_pct = max(segs, key=lambda s: s["end"])["hw_pct"]
        else:
            d_nonces = sum(s["d_nonces"] for s in segs)
            hw_pct = 100.0 * sum(s["d_hw"] for s in segs) / d_nonces if d_nonces > 0 else 0.0
        watts_n = sum(s["watts_n"] for s in segs)
        watts = sum(s["watts"] * s["watts_n"] for s in segs if s["watts"] is not None) / watts_n if watts_n else None
        mhs = sum(s["mhs"] * s["samples"] for s in segs) / n
        out.append({
            "clock": clock, "fan_target": target, "segments": len(segs),
            "start": min(s["start"] for s in segs), "end": max(s["end"] for s in segs),
            "minutes": hours * 60.0, "samples": n, "last": any(s["last"] for s in segs),
            "worst_chip": worst["worst_chip"] if worst else None,
            "bad_pct_min": min(s["bad_pct"] for s in with_bad) if with_bad else None,
            "bad_pct_max": worst["bad_pct"] if worst else None,
            "bad_partial": any(s["bad_partial"] for s in with_bad),
            "bad_per_hour": sum(s["d_bad"] for s in segs) / bad_hours if bad_hours > 0 else 0.0,
            "resets": sum(s["resets"] for s in segs),
            "hw_pct": hw_pct, "hw_approx": approx,
            "accepted_per_hour": sum(s["d_accepted"] for s in segs) / hours if hours > 0 else 0.0,
            "mhs": mhs,
            "chip_temp": sum(s["chip_temp"] * s["samples"] for s in segs) / n,
            "fan_rpm": sum(s["fan_rpm"] * s["samples"] for s in segs) / n,
            "overheat": sum(s["overheat"] for s in segs),
            "watts": watts, "watts_n": watts_n, "gh_per_w": _gh_per_w(mhs, watts),
        })
    out.sort(key=lambda r: r["end"], reverse=True)
    return out


def table(path, min_minutes=MIN_MINUTES, gap_s=GAP_S):
    """Everything the dashboard and the CLI show: rollup rows and segments, both newest first."""
    summaries = [summarize(s, min_minutes) for s in segments(read_rows(path), gap_s)]
    return {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "min_minutes": min_minutes,
        "rollup": rollup(summaries),
        "segments": list(reversed(summaries)),
    }


# ---------------------------------------------------------------- text output

def _pct_range(r):
    lo, hi = r.get("bad_pct_min", r.get("bad_pct")), r.get("bad_pct_max", r.get("bad_pct"))
    if hi is None:
        return "none"
    text = "%.1f%%" % hi if lo is None or abs(hi - lo) < 0.05 else "%.1f-%.1f%%" % (lo, hi)
    if r["bad_partial"]:
        text = ">=" + text
    return text


def _hash(mhs):
    unit, div = api.hashrate_unit(mhs)
    return "%.0f %s" % (mhs / div, unit)


def format_table(t, segments=False):
    """The rollup (or every non-short segment) as aligned text, newest first."""
    rows = [s for s in t["segments"] if not s["short"]] if segments else t["rollup"]
    head = "%-5s %-4s %-16s %7s %4s  %-14s %6s %6s %7s %7s %10s %5s %6s %5s %5s" % (
        "clock", "fan", "from", "min", "segs", "worst chip bad", "bad/h", "resets", "HW%", "acc/h", "hashrate", "watts", "GH/s/W", "temp", "rpm")
    lines = [head]
    for r in rows:
        chip = "none" if r["worst_chip"] is None else "chip %d %s" % (r["worst_chip"], _pct_range(r))
        lines.append("%-5d %-4s %-16s %7.0f %4s  %-14s %6.1f %6d %7s %7.0f %10s %5s %6s %5.1f %5.0f" % (
            r["clock"], "?" if r["fan_target"] is None else r["fan_target"], r["start"][:16], r["minutes"],
            r.get("segments", ""), chip, r["bad_per_hour"], r["resets"],
            ("~%.2f" if r["hw_approx"] else "%.2f") % r["hw_pct"], r["accepted_per_hour"],
            _hash(r["mhs"]), "?" if r["watts"] is None else "%.0f" % r["watts"],
            "?" if r["gh_per_w"] is None else "%.2f" % r["gh_per_w"], r["chip_temp"], r["fan_rpm"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- the unattended runner

def _ts(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _epoch(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def run_trial(miner, clocks, hours, end=None, fan=None, settle_min=10, check_min=5, min_judge_min=30,
              max_resets=0, max_bad=3.0, stale_min=10, log_path=None, status_path=None, report=None,
              now=time.time, sleep=time.sleep, out=print):
    """Hold each clock in `clocks` for `hours`, judging the service log every `check_min` minutes.

    A step is abandoned, and the whole run with it, when the board resets more
    than `max_resets` times during the step, when the worst chip's bad share is
    above `max_bad` percent once the step is `min_judge_min` old, or when the
    service log has no sample newer than `stale_min` minutes (the runner cannot
    judge blind). Whatever happens, including Ctrl-C, the miner is left on
    `end` (default: the lowest clock in the list). Every clock is checked
    against the firmware's range before anything is sent.

    `report(text)` gets one line per step for the event log; `status_path`
    receives a JSON progress file the dashboard shows, removed at the end.
    `now` and `sleep` are injectable so the state machine tests in milliseconds.
    """
    clocks = [int(c) for c in clocks]
    if not clocks:
        raise ValueError("no clocks to try")
    top = api.max_preset_mhz(miner.setting()) or 725
    end = min(clocks) if end is None else int(end)
    for c in clocks + [end]:
        if c % 25 or not 300 <= c <= top:
            raise ValueError("%d MHz: clocks must be multiples of 25 between 300 and %d MHz" % (c, top))
    report = report or (lambda text: None)
    log_path = Path(log_path) if log_path else None
    status_path = Path(status_path) if status_path else None
    state = {"clocks": clocks, "hours": hours, "end": end, "fan": fan, "started": _ts(now()), "step": 0,
             "clock": None, "status": "starting", "step_started": None, "step_ends": None, "checks": 0, "reason": None}
    result = {"completed": False, "step": None, "clock": None, "reason": None}

    def write_status():
        if status_path:
            tmp = status_path.with_name(status_path.name + ".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            tmp.replace(status_path)

    def judge(mhz, step_start):
        """The reason to stop, or None."""
        segs = table(log_path, min_minutes=0)["segments"] if log_path else []
        if not segs or now() - _epoch(segs[0]["end"]) > stale_min * 60:
            return "no fresh samples in the service log for %d min (is gbox serve running?)" % stale_min
        cur = next((s for s in segs if s["clock"] == mhz and _epoch(s["end"]) >= step_start), None)
        if cur is None:
            return None
        if cur["resets"] > max_resets:
            return "board reset %d time%s at %d MHz" % (cur["resets"], "" if cur["resets"] == 1 else "s", mhz)
        if cur["minutes"] >= min_judge_min and cur["bad_pct"] is not None and cur["bad_pct"] > max_bad:
            return "chip %d bad share %.1f%% at %d MHz, above the %.1f%% limit" % (cur["worst_chip"], cur["bad_pct"], mhz, max_bad)
        return None

    current = None
    try:
        if fan is not None:
            applied = miner.set_fan_target(fan)
            out("fan target set to %s C" % applied)
            report("trial: fan target set to %s C" % applied)
        for i, mhz in enumerate(clocks, 1):
            state.update(step=i, clock=mhz, status="settling", step_started=_ts(now()), checks=0,
                         step_ends=_ts(now() + settle_min * 60 + hours * 3600))
            result.update(step=i, clock=mhz)
            miner.set_plan(mhz)
            current = mhz
            msg = "trial: step %d/%d, clock set to %d MHz, holding %g h after %g min settle" % (i, len(clocks), mhz, hours, settle_min)
            out(_ts(now()), msg)
            report(msg)
            write_status()
            sleep(settle_min * 60)
            hold_start = now()
            state["status"] = "holding"
            while True:
                remaining = hold_start + hours * 3600 - now()
                if remaining <= 0:
                    break
                write_status()
                sleep(min(check_min * 60, remaining))
                state["checks"] += 1
                reason = judge(mhz, hold_start)
                if reason:
                    result["reason"] = reason
                    out(_ts(now()), "stopping:", reason)
                    break
            if result["reason"]:
                break
        else:
            result["completed"] = True
    except KeyboardInterrupt:
        result["reason"] = "interrupted from the keyboard"
        out(_ts(now()), "interrupted")
    except Exception as e:
        result["reason"] = "error: %s" % e
        out(_ts(now()), "error:", e)
    finally:
        state.update(status="done" if result["completed"] else "aborted", reason=result["reason"])
        try:
            if current != end:
                miner.set_plan(end)
            if result["completed"]:
                msg = "trial: finished all %d step%s, clock set to %d MHz (end)" % (len(clocks), "" if len(clocks) == 1 else "s", end)
            else:
                msg = "trial: aborted at step %s (%s MHz): %s; clock set to %d MHz (end)" % (
                    result["step"], result["clock"], result["reason"], end)
            out(_ts(now()), msg)
            report(msg)
        except Exception as e:                       # the end clock could not be applied: say so loudly
            out(_ts(now()), "COULD NOT SET THE END CLOCK %d MHz: %s. Set it by hand." % (end, e))
            report("trial: could not set the end clock %d MHz: %s" % (end, e))
        finally:
            if status_path and status_path.exists():
                status_path.unlink()
    return result
