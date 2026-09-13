"""Write a three-day synthetic log.csv (and events.log) for looking at the charts on a scratch service.

    python -m tests.make_synthetic_log --data <dir>

The shape: 30-second samples for 72 hours ending now, 575 MHz until 24 hours
ago then 550, a board-reset burst with a bad-nonce spike about 30 hours ago,
a boot (counters restart) about 10 hours ago, a two-hour hole 50 hours ago,
chip 8 with a small steady bad share, watts from a plug, and event lines at
the burst and the boot. Not a test; a fixture generator for the eye.
"""
import argparse
import datetime
import os
import random

from gbox.poller import COLUMNS

HEADER = ",".join(COLUMNS)


def main(argv=None):
    p = argparse.ArgumentParser(description="write a synthetic three-day log for the charts")
    p.add_argument("--data", required=True, help="data directory to write log.csv and events.log into")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)
    rng = random.Random(a.seed)
    os.makedirs(a.data, exist_ok=True)
    now = datetime.datetime.now().replace(second=0, microsecond=0)
    start = now - datetime.timedelta(hours=72)
    t_burst = now - datetime.timedelta(hours=30)
    t_boot = now - datetime.timedelta(hours=10)
    t_clock = now - datetime.timedelta(hours=24)
    hole = (now - datetime.timedelta(hours=50), now - datetime.timedelta(hours=48))
    good, bad, resets, elapsed, acc = 0, 0, 0, 0, 0
    chips = {c: [0, 0] for c in range(1, 17)}
    rows, events = [], []
    t = start
    while t <= now:
        if hole[0] <= t < hole[1]:
            t += datetime.timedelta(seconds=30)
            continue
        clock = 575.0 if t < t_clock else 550.0
        in_burst = t_burst <= t < t_burst + datetime.timedelta(minutes=20)
        if t == t_boot or (t_boot < t < t_boot + datetime.timedelta(seconds=30)):
            good, bad, resets, elapsed, acc = 0, 0, 0, 0, 0
            chips = {c: [0, 0] for c in range(1, 17)}
            events.append("%s power: cycled #1 today: off 15 s, on (miner unreachable for 2 min; 36 W before)" % t.strftime("%Y-%m-%d %H:%M:%S"))
        elapsed += 30
        for c in chips:
            g = rng.randint(4, 6)
            b = 0
            if c == 8:
                b = 1 if rng.random() < (0.6 if in_burst else 0.02) else 0
                if in_burst:
                    g = rng.randint(0, 2)
            chips[c][0] += g
            chips[c][1] += b
            good += g
            bad += b
        if in_burst and rng.random() < 0.15:
            resets += 1
        acc += 1 if rng.random() < 0.6 else 0
        mhs = (clock / 575.0) * 735000 * (0.55 if in_burst else 1.0) * rng.uniform(0.93, 1.07)
        chip_t = 40 if in_burst else 70
        watts = 190 + (0 if clock == 550 else 7) + rng.uniform(-2, 2)
        weak = "8:%d/%d" % (chips[8][0], chips[8][1]) if chips[8][1] > 40 else ""
        chips_col = ";".join("0.%d:%d/%d" % (c, v[0], v[1]) for c, v in chips.items())
        hwpct = 100.0 * bad / max(1, good + bad)
        rows.append(",".join(str(x) for x in [
            t.strftime("%Y-%m-%d %H:%M:%S"), "ok", elapsed, "%.3f" % mhs, "%.3f" % mhs, bad, "%.4f" % hwpct, acc, 0, clock,
            1260, 1260, chip_t, chip_t, chip_t - 8, resets, weak, good, bad, 65, 0, "%.2f" % watts, chips_col]))
        t += datetime.timedelta(seconds=30)
    if t_burst >= start:
        events.append("%s watchdog: restart #1 sent (accepted shares frozen for 5 min)" % (t_burst + datetime.timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"))
    events.append("%s dashboard: clock set to 550 MHz (plan \"550 MHz 0.41 V 90 RPM 90 RPM\", was \"575 MHz 0.41 V 90 RPM 90 RPM\")" % t_clock.strftime("%Y-%m-%d %H:%M:%S"))
    events.sort()
    with open(os.path.join(a.data, "log.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(HEADER + "\n" + "\n".join(rows) + "\n")
    with open(os.path.join(a.data, "events.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(events) + "\n")
    print("wrote %d rows and %d events to %s" % (len(rows), len(events), a.data))


if __name__ == "__main__":
    main()
