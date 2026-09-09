# Finding the right clock

A marginal chip shows up as bad nonces, then as board resets, then as a
miner that reports 30 percent of its rated hashrate. The stock UI cannot see
any of that and offers only the factory presets. The dashboard's **Clock**
control sets the clock in 25 MHz steps, and the service logs enough to
compare clocks fairly. This page is the method: by hand, or unattended with
`gbox trials run`. For what each column of the result means, see
[Reading the table](#reading-the-table).

## What you are trading

Lowering the clock lowers hashrate for every chip, but a chip that produces
bad nonces at 600 MHz can be clean at 575, and a chip that resets the board
costs far more than 4 percent of throughput. On the SC-BOX this toolkit was
written for, 575 MHz gave about 97 percent of 600 MHz's hashrate, with the
weak chip's bad share falling from 8 percent to 0.1 percent and zero board
resets. Your miner is a different miner. Measure it.

## Before you start

- The service must be running (`gbox serve`, or the installed service),
  because the table is built from its log. Open the dashboard and log in
  once so the service can poll.
- Know the factory preset clock. The **Clock** tile shows the current clock
  and plan. Never press **Save** on the miner's own Miner page while a trial
  runs: it silently reverts to the preset.
- Do not restart the miner unless it has stopped hashing. A restart ends
  the current run in the table.

## Manual trial

1. On the dashboard, note the current clock and the **Board resets** tile.
2. Pick a starting clock. If the miner is resetting the board or a chip is
   flagged failing, start low: 100 MHz under the preset, or 500 MHz if in
   doubt. If it is only showing bad nonces, start 25 MHz under.
3. To set it, use the **Clock** control. The dialog shows the exact change
   and asks for the miner password. The fans spike for a few minutes after
   any settings change; ignore the first ten minutes.
4. Leave it. After four hours, or the next morning, read the new row in
   the **Clock trials** table. Zero resets and a bad share near zero means
   the chip is fine at this clock.
5. If the row was clean, move 25 MHz up; if not, 25 MHz down. Hold again.
   Stop climbing at the first clock that shows resets or a rising bad
   share. The clock one step under it is your answer.
6. Optionally, repeat the best clock with a different fan target, using the
   **Fan target** control (65 to 75). A higher target means slower, quieter
   fans and hotter chips. The table's chip temperature and fan columns show
   what you bought, and the bad share and resets columns show whether the
   chip minded.

## Unattended trial

`gbox trials run` steps through a list of clocks by itself. It reads the
service's log to decide when to stop, so it refuses to start when the
service is not running.

```
gbox trials run 550 575 600 --hours 4 --end 575
```

This holds 550 MHz for four hours, then 575, then 600, and leaves the miner
at 575 MHz when it finishes or stops early. It asks for the miner password
once, at the start, or reads `GBOX_PASSWORD`.

At each step the runner does the following:

- Checks every clock in the list against the firmware's range before it
  sends anything, so a typo cannot start a 725 MHz step at 3 a.m.
- Sets the clock, waits ten minutes for the fans to settle (`--settle`),
  then holds for `--hours`.
- Checks the log every five minutes (`--check`). If the board has reset
  (`--max-resets`, default 0), or if the worst chip's bad share is over
  `--max-bad` percent (default 3) once the step is 30 minutes old
  (`--judge`), it stops the whole run and sets the `--end` clock. A clock
  that trips a guard is the answer; there is no point trying the next one
  up. It also stops if the service log goes quiet for ten minutes, because
  it cannot judge blind.
- Writes one line to the event log per step. The dashboard's fan chart
  shows a marker, and the **Clock trials** section shows "Trial running:
  step 2 of 3" with the time held so far.

To stop the run early, press Ctrl-C in its terminal. The runner sets the
`--end` clock before it exits. If the terminal is closed or the PC sleeps,
the miner keeps whatever clock it was on, and the next `gbox trials` row
shows which. `--end` defaults to the lowest clock in the list, so an
interrupted run never leaves the miner higher than where it started.

To compare fan settings, add `--fan 65` to set the fan target once at the
start, then run the same list again with a different `--fan`.

Order the list from safe to aggressive, as in the example. The run stops at
the first bad clock, and you already have the rows for the clocks under it.
The runner exits with status 2 when it aborted and 0 when it finished the
list.

To read the result, open the table the next morning. Take the highest clock
with zero resets and a worst-chip bad share you accept (under 1 percent is
comfortable; the SC-BOX case accepted 0.1 percent), and set it with the
**Clock** control or `gbox plan`. It survives restarts.

## Reading the table

The **Clock trials** table on the dashboard, and `gbox trials` in a
terminal, have one row per clock and fan target the miner has run since the
service started logging. The columns:

- **Held.** How long the clock was held, and over how many separate runs.
  Under about an hour the error columns are noise. Give each clock at least
  four hours, longer if the bad share is still moving.
- **Worst chip, bad share.** The chip with the highest share of bad nonces
  in that run, and its share. Compare shares, not counts: a slower clock
  attempts fewer nonces per hour. "None flagged" means no chip was weak
  enough for the logger to flag at that clock, which is the result you
  want. A ≥ in front means the chip dropped off the weak list part-way
  through, so the figure covers only the time the logger flagged it. That
  is an improvement, not a fault.
- **Bad/hour.** The same chip's bad nonces per hour, for a feel of scale.
- **Board resets.** Any number over zero is the chip forcing a board
  restart. This is the column that ends a trial.
- **HW error.** Bad nonces as a share of all nonces the board produced in
  the run. For samples logged before this version kept nonce totals, it is
  the firmware's running average instead and shows a ~.
- **Accepted/hr.** Shares the pool accepted per hour. This is what you are
  paid on, but pools adjust the share difficulty per connection, so this
  number can change by a factor of two without the miner doing anything
  different. The SC-BOX log has a 1521/hr run and a 643/hr run at the same
  clock and the same hashrate. Compare it only within one pool session, and
  use the hashrate column for throughput.
- **Hashrate.** Mean 20-second hashrate over the run. The honest throughput
  number.
- **Chip temp, fans.** Mean chip temperature and fan speed, and how many
  samples had the overheat flag set. Lower is less stress and less noise at
  the same output.

To see the runs separately instead of one row per clock, tick **show each
continuous run**. A run ends when the clock or fan target changes, the
miner restarts, the pool connection resets its share counter, or the
service missed more than five minutes of samples. The table hides runs
shorter than 20 minutes; `gbox trials --min 5` lowers the cutoff. The live
run is in bold.
