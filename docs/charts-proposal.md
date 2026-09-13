# Proposal: the operator's view over days (0.6.0)

Agreed with the owner on 2026-09-13 evening, after a day in which the log
recorded a 34-reset cold start, a boardless power-on, and a 46-reset chain
silence, and none of it was visible on the page for more than a few hours.
His words for the purpose: "for the human operator to see when things
really went bad on a graph, so she/he can do something." Alerting is a
later step, not this one. The other-models work stays separate (0.7.0).

## What changes, in one paragraph

Every chart on the served page draws from the service log over a long
window instead of the miner's five-hour buffer: at least 24 hours for the
existing hashrate, fan, temperature and power charts, and a new chart of
errors over three days. A new endpoint serves the log as bucketed series,
computed once per log change and cached. The poller logs every chip's
nonce counts every poll in one appended column, written with the board
number so a multi-board unit needs no second format. Nothing about the
miner requests changes.

## 1. The chips column

`log.csv` gains a 23rd column, `chips`, appended after `watts`: every
chip's cumulative good and bad nonce counts as the firmware reports them,
`board.chip:good/bad` joined with `;`, in board then chip order. On the
SC-BOX that is 16 entries and about 130 characters a row, or 375 KB a
day. The data comes from the `icinfo` request the poller already makes,
so a bigger unit costs disk, not controller time. `weak_chips` stays as
it is. `migrate_columns` pads an older log with a `.bak` copy, as it did
from 17 to 21 and 21 to 22 columns. Counters reset whenever the firmware
reinitializes the board; the bucketing rule below handles that.

## 2. The series endpoint

`GET /api/series?hours=H&bucket=M` answers JSON:

```
{"from": "...", "to": "...", "bucket_minutes": M, "buckets": [
   {"t": "2026-09-13 07:00", "samples": 60, "errors": 0,
    "hashrate": 703000.0, "fan0": 1260, "fan1": 1260, "chip_temp": 70.0, "board_temp": 61.4, "watts": 191.0,
    "clock": 550.0, "good": 5146, "bad": 9, "share": 0.17, "resets": 46,
    "worst": {"chip": "0.8", "good": 320, "bad": 4, "share": 1.23}},
   ...],
 "events": [{"t": "2026-09-13 07:00:46", "label": "power: cycled #3 today ..."}, ...]}
```

- `hours` 1 to 168, `bucket` 5 to 120 minutes; 400 for anything else.
  The page asks for 24 h at 5 min for the existing charts and 72 h at 30
  min for the errors chart.
- Means over the bucket's good samples for hashrate (`mhs_20s`), fans,
  temperatures and watts; the last good sample's clock; a bucket with no
  good sample carries nulls and draws as a gap, never as zero.
- Counts are sums of row-to-row increments inside the bucket, with the
  rule the hour tile and the trials table already use: an increment is
  the difference when the counter grew, and the new value when it fell
  (the counter restarted). `good`, `bad` and `resets` follow it; `share`
  is `bad / (good + bad)` in percent, null when both are zero.
- `worst` is the chip with the highest share in the bucket among chips
  with at least one nonce, computed from the chips column per chip with
  the same increment rule; null before the column existed, except that
  rows from before 0.6.0 fall back to `weak_chips`, which names chip 8
  in most hours of the last three days.
- Events are the same lines the fan chart marks today, within the window.
- Cached in `ServiceState` keyed by the log's modification time and size
  and the two parameters, like the trials table. A 72-hour window is
  8,640 rows and 16 chips each; if the first cut takes more than a second
  on the owner's PC, the cache keeps finished buckets and recomputes only
  the last one.

## 3. The page

**The errors chart**, a new section under Fans and temperature, served
pages only (the standalone file has no log; it says so, as the trials
table does). Time on x over three days, day boundaries labeled with the
date; bad share of all chips per bucket and the worst chip's share as two
lines on the left axis in percent; the clock as a step line on a right
axis in MHz; board resets per bucket as marks along the bottom, taller
with more resets; the event markers with their glyphs. Hover shows the
bucket's time range, bad and total nonces, the worst chip's id and
counts, the clock, and the resets. This morning's 07:00 bucket reads
about 74% share with 34 resets; the 16:02 bucket reads 46 resets with a
flat share, which is the point of drawing both.

**The existing charts** switch to the endpoint at 24 hours and 5-minute
buckets when served: hashrate from the log's 20-second readings (the
miner's buffer remains the source for the standalone file and for the
"now" tile), fans and both temperatures, watts, each with its right-hand
percent-of-rated axis as today, and the event markers. The x axis gains
the day label. The page stops fetching the whole CSV every minute;
`/api/log.csv` stays for download.

## 4. CLI

`gbox errors [--hours 72] [--bucket 30]` prints the buckets as a table
(time, clock, bad share, worst chip and its share, resets, hashrate,
watts) from the same function, for a terminal or a screenshot.

## 5. Tests and done-when

- Series: a synthetic log spanning a boot and a board reinit; buckets,
  increments across the resets, the worst chip and its fallback to
  `weak_chips` on old rows, empty buckets as nulls, parameter limits.
- Migration 22 to 23 columns with the `.bak`; the poller writes the chips
  column in board.chip order.
- Endpoint: shape, caching by mtime, 400s.
- Page under Node: bucket-to-chart mapping, hover text, day labels.
- Chrome on a scratch service with a three-day synthetic log; then the
  live service.
- Done-when, on the live page: the three-day chart shows the 07:00 burst
  and the 16:02 resets with the 14:20 clock step between them; the
  24-hour charts show the day's cycles and the hold; `gbox errors`
  prints the same buckets.

## 6. Version, docs, what is deferred

0.6.0: the log format, the HTTP API and the CLI all change. README, the
guide's event-line table (unchanged lines, a new section on reading the
charts), `plan.md` decision rows, `firmware-api.md` where the chips data
is described. Deferred, with the reason: log rotation (the chips column
makes it matter; it goes with the multi-board work in 0.7.0 where the
row grows further); alerting (Mark: later); a clock-on-x scatter (the
trials table covers it per run; add only if the time chart leaves him
wanting it); the standalone file's charts (no log to draw from).

## Assumptions to check

- That `mhs_20s` averaged over five minutes is a fair hashrate line; the
  miner's own buffer is per minute and the two will differ slightly.
- That the worst chip in a 30-minute bucket at 550 MHz has enough nonces
  to mean something: about 270 per chip per bucket, so one bad nonce is
  0.4%. Hover shows the counts so a one-nonce spike reads as one.
