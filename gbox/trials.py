"""Clock trials: the logger's log.csv cut into segments per clock and fan target.

A segment is a run of good samples at one clock and one fan target with no
restart (the miner's counters reset), no pool reset (accepted drops) and no
hole in the log longer than `gap_s`. Each segment is summarized from the
counter deltas between its first and last sample, never from the miner's
running totals, so clocks held on different days compare fairly. `rollup`
pools the segments of one (clock, fan target) pair into the row the
dashboard and `gbox trials` show by default.

Pure functions over the CSV; nothing here talks to the miner.
"""
import csv
import datetime
import re
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
            seg = {"first": row, "last": row, "n": 0, "mhs": 0.0, "temp": 0.0, "fan": 0.0, "overheat": 0, "chips": {}}
            segs.append(seg)
        seg["last"] = row
        seg["n"] += 1
        seg["mhs"] += row["mhs_20s"]
        seg["temp"] += row["tstemp0"]
        seg["fan"] += (row["fan0"] + row["fan1"]) / 2.0
        seg["overheat"] += 1 if row["overheat"] else 0
        for chip, gb in row["chips"].items():
            c = seg["chips"].setdefault(chip, {"first": gb, "first_t": row["t"]})
            c["last"], c["last_t"] = gb, row["t"]
    for s in segs:
        s["last_segment"] = s is segs[-1]
    return segs


def _hours(a, b):
    return (b - a).total_seconds() / 3600.0


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
        "mhs": seg["mhs"] / n, "chip_temp": seg["temp"] / n, "fan_rpm": seg["fan"] / n, "overheat": seg["overheat"],
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
            "mhs": sum(s["mhs"] * s["samples"] for s in segs) / n,
            "chip_temp": sum(s["chip_temp"] * s["samples"] for s in segs) / n,
            "fan_rpm": sum(s["fan_rpm"] * s["samples"] for s in segs) / n,
            "overheat": sum(s["overheat"] for s in segs),
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
    head = "%-5s %-4s %-16s %7s %4s  %-14s %6s %6s %7s %7s %10s %5s %5s" % (
        "clock", "fan", "from", "min", "segs", "worst chip bad", "bad/h", "resets", "HW%", "acc/h", "hashrate", "temp", "rpm")
    lines = [head]
    for r in rows:
        chip = "none" if r["worst_chip"] is None else "chip %d %s" % (r["worst_chip"], _pct_range(r))
        lines.append("%-5d %-4s %-16s %7.0f %4s  %-14s %6.1f %6d %7s %7.0f %10s %5.1f %5.0f" % (
            r["clock"], "?" if r["fan_target"] is None else r["fan_target"], r["start"][:16], r["minutes"],
            r.get("segments", ""), chip, r["bad_per_hour"], r["resets"],
            ("~%.2f" if r["hw_approx"] else "%.2f") % r["hw_pct"], r["accepted_per_hour"],
            _hash(r["mhs"]), r["chip_temp"], r["fan_rpm"]))
    return "\n".join(lines)
