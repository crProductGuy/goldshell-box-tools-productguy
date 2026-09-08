"""Bad-nonce rate of one chip per clock segment, from the gbox logger's log.csv.

    python scripts/chip-rates.py            # chip 8, ~/.gbox/log.csv
    python scripts/chip-rates.py 3 path/to/log.csv

The logger writes the cumulative good/bad nonce counts of every weak or
failing chip in the `weak_chips` column, so a chip that improves enough to
lose its flag disappears from the output: that is a good result, not a bug.
Segments shorter than 20 minutes are skipped. Compare the bad share between
clocks, not the bad count: a slower clock attempts fewer nonces per hour.
"""
import csv
import os
import re
import sys
from datetime import datetime


def main(chip=8, path=None):
    path = path or os.path.expanduser(os.environ.get("GBOX_DATA", "~/.gbox") + "/log.csv")
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["http"] == "ok" and r["weak_chips"]]
    pat = re.compile(r"(?:^|;)%d:(\d+)/(\d+)" % chip)
    segs = []
    for r in rows:
        m = pat.search(r["weak_chips"])
        if not m:
            continue
        v = (int(m.group(1)), int(m.group(2)))
        t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S")
        if segs and segs[-1]["clock"] == r["clock"] and (t - segs[-1]["end"]).total_seconds() < 300:
            segs[-1]["end"], segs[-1]["last"] = t, v
        else:
            segs.append({"clock": r["clock"], "start": t, "end": t, "first": v, "last": v})
    print("chip %d  %-6s %-13s %-13s %7s %9s %8s %7s %7s" % (chip, "clock", "from", "to", "min", "good", "bad", "bad%", "bad/h"))
    for s in segs:
        mins = (s["end"] - s["start"]).total_seconds() / 60
        dg, db = s["last"][0] - s["first"][0], s["last"][1] - s["first"][1]
        if mins < 20 or dg + db <= 0:
            continue
        print("        %-6s %s %s %7.0f %9d %8d %6.2f%% %7.1f" % (
            s["clock"], s["start"].strftime("%m-%d %H:%M"), s["end"].strftime("%m-%d %H:%M"), mins, dg, db, 100 * db / (dg + db), 60 * db / mins))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8, sys.argv[2] if len(sys.argv) > 2 else None)
