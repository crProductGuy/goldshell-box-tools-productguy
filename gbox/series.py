"""The service log bucketed for the charts: means, counter increments, the worst chip, events.

The charts need days, not the miner's five-hour buffer, and the page must not
pull the whole CSV every minute. This module cuts `log.csv` into buckets of
`bucket_minutes` over the last `hours` and answers with one record per
bucket. Counts (good and bad nonces, board resets) are sums of row-to-row
increments with the rule the hour tile and the trials table already use:
an increment is the difference when the counter grew, and the new value
when it fell, because the firmware restarts its counters at every boot and
board reinit. An increment counts in the bucket of the later sample. Means
(hashrate, fans, temperatures, watts) are over the bucket's good samples; a
bucket with no good sample carries None everywhere and draws as a gap.

The worst chip is the one with the highest bad share in the bucket, from the
`chips` column (0.6.0) when both rows of a pair carry it, else from the
`weak_chips` column for chips flagged in both rows, which is what rows from
before 0.6.0 have. Design of record: docs/charts-proposal.md.
"""
import csv
import datetime
import re
from pathlib import Path

from . import api

MIN_HOURS, MAX_HOURS = 1, 168
MIN_BUCKET, MAX_BUCKET = 5, 120
NO_READING = -100.0     # a board-sensor value at or below this is the firmware saying "no sensor", not a temperature
STAMP = "%Y-%m-%d %H:%M:%S"
_FLOATS = ("mhs_20s", "clock", "watts", "fan0", "fan1", "tstemp0", "tstemp2", "hot_peak", "hot_level", "chip_avg")
_INTS = ("nonces_good", "nonces_bad", "rebootcnt", "hwerr", "accepted", "elapsed")
_WEAK_RE = re.compile(r"(\d+):(\d+)/(\d+)")
_EVENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ((?:dashboard|watchdog|power|hold): .*|service: started .*)$")


def inc(prev, cur):
    """The counter's increment between two samples, or None when either is missing."""
    if prev is None or cur is None:
        return None
    return cur - prev if cur >= prev else cur


def _num(s, cast):
    try:
        return cast(float(s))
    except (TypeError, ValueError):
        return None


def _weak(text):
    return {"0.%s" % m.group(1): (int(m.group(2)), int(m.group(3))) for m in _WEAK_RE.finditer(text or "")}


def read_rows(path):
    """Every row of log.csv, ok or failed, with numbers parsed (None when blank) and the chip columns as dicts."""
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                t = datetime.datetime.strptime(r.get("time") or "", STAMP)
            except ValueError:
                continue
            ok = r.get("http") == "ok"
            row = {"t": t, "ok": ok}
            for k in _FLOATS:
                row[k] = _num(r.get(k), float)
            for k in _INTS:
                row[k] = _num(r.get(k), int)
            if row["tstemp2"] is not None and row["tstemp2"] <= NO_READING:
                # the firmware reports the board sensor as -150 and the chip as 0 with the hashboard absent
                # (2026-09-13 15:46): no reading, not a temperature
                row["tstemp2"] = None
                if row["tstemp0"] == 0:
                    row["tstemp0"] = None
            chips_text = r.get("chips") or ""
            row["chips"] = api.parse_chips(chips_text) if chips_text else None
            row["weak"] = _weak(r.get("weak_chips"))
            out.append(row)
    return out


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pair_chips(prev, cur):
    """Per-chip (good, bad) increments between two ok rows, from the chips column or the weak flags."""
    a, b = (prev["chips"], cur["chips"]) if prev["chips"] and cur["chips"] else (prev["weak"], cur["weak"])
    out = {}
    for key in b:
        if key in a:
            out[key] = (inc(a[key][0], b[key][0]), inc(a[key][1], b[key][1]))
    return out


def buckets(rows, hours, bucket_minutes, now=None, events=()):
    """The spec's series record for the last `hours`, in `bucket_minutes` buckets aligned to local midnight."""
    if not (MIN_HOURS <= int(hours) <= MAX_HOURS):
        raise ValueError("hours must be %d to %d" % (MIN_HOURS, MAX_HOURS))
    if not (MIN_BUCKET <= int(bucket_minutes) <= MAX_BUCKET):
        raise ValueError("bucket must be %d to %d minutes" % (MIN_BUCKET, MAX_BUCKET))
    hours, bucket_minutes = int(hours), int(bucket_minutes)
    now = now or datetime.datetime.now()
    width = datetime.timedelta(minutes=bucket_minutes)
    raw_start = now - datetime.timedelta(hours=hours)
    midnight = raw_start.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = int((raw_start - midnight).total_seconds() // 60)
    start = midnight + datetime.timedelta(minutes=minutes - minutes % bucket_minutes)
    edges = []
    edge = start
    while edge <= now:
        edges.append(edge)
        edge += width
    index = {e: i for i, e in enumerate(edges)}

    def bucket_of(t):
        if t < start or t > now:
            return None
        k = int((t - start).total_seconds() // (bucket_minutes * 60))
        return k if k < len(edges) else None

    acc = [{"ok": [], "errors": 0, "good": 0, "bad": 0, "resets": 0, "counted": False, "chips": {}} for _ in edges]
    prev_ok = None
    for r in sorted(rows, key=lambda x: x["t"]):
        k = bucket_of(r["t"])
        if not r["ok"]:
            if k is not None:
                acc[k]["errors"] += 1
            continue
        if k is not None:
            a = acc[k]
            a["ok"].append(r)
            if prev_ok is not None:
                for key, src in (("good", "nonces_good"), ("bad", "nonces_bad"), ("resets", "rebootcnt")):
                    d = inc(prev_ok[src], r[src])
                    if d is not None:
                        a[key] += d
                        a["counted"] = True
                for chip, (g, b) in _pair_chips(prev_ok, r).items():
                    cg, cb = a["chips"].get(chip, (0, 0))
                    a["chips"][chip] = (cg + (g or 0), cb + (b or 0))
        prev_ok = r

    out = []
    for e, a in zip(edges, acc):
        rec = {"t": e.strftime("%Y-%m-%d %H:%M"), "samples": len(a["ok"]), "errors": a["errors"]}
        if not a["ok"]:
            rec.update({"hashrate": None, "fan0": None, "fan1": None, "chip_temp": None, "board_temp": None, "watts": None,
                        "clock": None, "good": None, "bad": None, "share": None, "resets": None, "worst": None,
                        "hot_peak": None, "hot_level": None, "chip_avg": None})
            out.append(rec)
            continue
        ok = a["ok"]
        rec["hashrate"] = _mean(r["mhs_20s"] for r in ok)
        rec["fan0"] = _mean(r["fan0"] for r in ok)
        rec["fan1"] = _mean(r["fan1"] for r in ok)
        rec["chip_temp"] = _mean(r["tstemp0"] for r in ok)
        rec["board_temp"] = _mean(r["tstemp2"] for r in ok)
        rec["watts"] = _mean(r["watts"] for r in ok)
        # 0.7.0, from the cgminer log on the rows that read it (most rows are blank): the bucket's highest peak,
        # its mean sustained level, its mean chip average
        peaks = [r.get("hot_peak") for r in ok if r.get("hot_peak") is not None]
        rec["hot_peak"] = max(peaks) if peaks else None
        rec["hot_level"] = _mean(r.get("hot_level") for r in ok)
        rec["chip_avg"] = _mean(r.get("chip_avg") for r in ok)
        rec["clock"] = ok[-1]["clock"]
        if a["counted"]:
            rec["good"], rec["bad"], rec["resets"] = a["good"], a["bad"], a["resets"]
            total = a["good"] + a["bad"]
            rec["share"] = 100.0 * a["bad"] / total if total else None
        else:
            rec["good"] = rec["bad"] = rec["resets"] = rec["share"] = None
        worst = None
        for chip, (g, b) in a["chips"].items():
            if g + b <= 0:
                continue
            share = 100.0 * b / (g + b)
            if worst is None or share > worst["share"]:
                worst = {"chip": chip, "good": g, "bad": b, "share": share}
        rec["worst"] = worst
        out.append(rec)

    ev = []
    for line in events:
        m = _EVENT_RE.match((line or "").rstrip("\r\n"))
        if not m:
            continue
        try:
            t = datetime.datetime.strptime(m.group(1), STAMP)
        except ValueError:
            continue
        if start <= t <= now:
            ev.append({"t": m.group(1), "label": m.group(2)})
    return {"from": start.strftime("%Y-%m-%d %H:%M"), "to": now.strftime("%Y-%m-%d %H:%M"), "hours": hours,
            "bucket_minutes": bucket_minutes, "buckets": out, "events": ev}


def format_table(result):
    """The buckets as a text table for `gbox errors`."""
    head = "%-16s %6s %9s %-22s %6s %10s %6s" % ("time", "clock", "bad share", "worst chip", "resets", "hashrate", "watts")
    lines = [head]
    for b in result["buckets"]:
        if b["samples"] == 0:
            lines.append("%-16s %6s %9s %-22s %6s %10s %6s" % (b["t"], "-", "-", "no samples", "-", "-", "-"))
            continue
        w = b["worst"]
        worst = "-" if not w else "%s %.2f%% (%d/%d)" % (w["chip"], w["share"], w["bad"], w["good"] + w["bad"])
        unit, div = api.hashrate_unit(b["hashrate"])
        lines.append("%-16s %6s %9s %-22s %6s %10s %6s" % (
            b["t"], "-" if b["clock"] is None else "%.0f" % b["clock"],
            "-" if b["share"] is None else "%.2f%%" % b["share"], worst,
            "-" if b["resets"] is None else str(b["resets"]),
            "-" if b["hashrate"] is None else "%.0f %s" % (b["hashrate"] / div, unit),
            "-" if b["watts"] is None else "%.0f" % b["watts"]))
    return "\n".join(lines)
