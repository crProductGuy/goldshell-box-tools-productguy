---
title: Goldshell BOX behavior and failure modes
---

# Goldshell BOX behavior and failure modes

What the counters mean, when the unit restarts itself, the failure modes of the
board and the controller, how the fans behave, and how to read the miner's own
log. Most of this is one SC-BOX (hardware 40.40.HA, MCB_V5_4, fw 2.2.5) watched
from 2026-09-05 onward. Treat every claim as that unit unless the tag names
another. Evidence: [notebook](../firmware-api.md).

## Counters, and what they can tell you

Evidence: [notebook, Counters](../firmware-api.md#counters-and-what-they-can-and-cannot-tell-you).

- `Device Hardware%` is a running average since the miner started, and it
  drifts for hours after a change. For the error rate at one clock, take the
  difference of `Hardware Errors` and of summed `perf` between two samples.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`
- `Device Elapsed`, `Accepted`, `Rejected`, `Hardware Errors` and `rebootcnt`
  all reset to zero on a controller restart (soft restart, power cycle or the
  watchdog). A drop in `Device Elapsed` is the reliable sign.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`. Compare the implied
  start time (`now - Elapsed`) rather than uptime, so a restart behind a long
  outage is still caught.
- `Accepted` can also reset without a restart when the pool connection is
  re-established. The same unit logged 1521 and 643 accepted shares per hour at
  the same clock and hashrate on two pool sessions. Use `MHS 20s` or summed
  `perf` for throughput. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`

## The unit restarts itself

- A replay of the SC-BOX's log from 2026-09-05 found 15 restarts with no
  restart, cycle or page action before them. Two of those fall across a gap when
  gbox was not running, so those are not certain. Three were on 2026-09-26/27 at
  525 MHz, each unreachable for 30 s to 1 min (connection refused, draw down to
  about 20 W, then hashing again). Cause unknown.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-27]`
- On 2026-09-21 two restarts had `Minerd start !!!` in `/dbg/syslog` with no
  `Restart Miner` line before them, no power loss (the clock did not fall back
  to 2007), and a pause of about 11 s in the backend's 3-second `[cmos]` lines.
  Nothing in either log names the cause.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-21]`
- A restart request that timed out on the client (18:32 that evening) never
  reached the miner. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-21]`
  ([evidence](../firmware-api.md#silent-supervisor-restarts))

## The fans start near full speed and ramp down

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-27]`

- After every restart the fans read 4,260 to 4,380 RPM (rated maximum about
  4,900) on a board at 43 to 51 C, falling to 1,200 to 1,260 RPM at 525 MHz over
  about 15 minutes.
- A clock or fan-target change does the same ramp without resetting
  `Device Elapsed`: six such surges in the replay, each within a minute of a
  clock or fan-target change. A fan surge alone does not say the unit restarted.
- The fan controller steers on the board sensor, so a small drop there buys a
  large fan-speed drop. An external fan lowered the board sensor 2.4 C (64.7 to
  62.3) and the internal fans fell from 1860 to 1200 RPM. `[verified: SC-BOX,
  MCB_V5_4, fw 2.2.5, 2026-09, day not recorded]`

## Three ways the SC-BOX fails

Evidence: [notebook, Hashboard behavior](../firmware-api.md#hashboard-behavior-seen-on-the-sc-box).

| Mode | What you see | How it ends | Tag |
|---|---|---|---|
| Reset burst | `rebootcnt` climbing by 8 or more a sample; `clock` and temperatures real, often mid-ramp; log line `Read Nonce Faild 10 Times, Reinit Device!!!` | by itself in 0 to 2.5 minutes (12 episodes) | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]` |
| Board absent | HTTP fine, `clock` 0, `tstemp-2` at -150, `rebootcnt` static, a few watts at the wall; log line `WatchDog Exit for CPB Idle...` every 5 s | never by itself; 6 to 22 minutes, each until a soft restart (6 episodes) | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]` |
| Controller hang | no HTTP, no ping, no ARP, fans still turning, about 34 to 53 W at the wall against 183 to 188 W hashing, barely warm air; the miner log shows runs of `Auto addressing failed`, `BistStart err`, `SEND JOB FAILD ... REINIT THIS CPB` and `WatchDog Exit for CPB Idle` around it (the controller losing its hashboard) | a power cut clears it; a soft restart cannot reach it; one episode ended by itself after 51 minutes (HTTP 500 during boot, then 34 W straight to 188 W) | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-09 and 2026-09-21]` |

- A second sub-mode of a failing board: it accepts work and reports each job
  finished instantly without hashing. The firmware notices after minutes via
  `SEND JOB FAILD 10 TIMES, REINIT THIS CPB`.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09, day not recorded]`
- The controller hang was seen 2026-09-06, 2026-09-09 19:00 and 2026-09-09
  22:22. Another hang on 2026-11-01: wall draw fell to about 12 W at 05:57.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- After a power cut the unit takes about a minute to answer again: 60 and 66 s
  after a 15 s cut, `rebootcnt` 0, `Device Elapsed` under 30 s on the first
  sample, one poll with HTTP 500 during boot.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-10]`
- **A manual power plan after a power cut:** earlier notes said it survives.
  `[contradicted: 2026-11-01, see settings-and-power-plans.md]`: it came back as
  the preset after one cut on 2026-11-01, though it survived more than 20 cold
  boots before. ([details](settings-and-power-plans.md#does-a-manual-plan-survive-a-power-cycle))
- A soft restart is not free: of four sent while the board was present, three
  lost the board and needed another. Of 13 sent to an absent board or an
  unreachable miner, none left it worse.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]`
- What gbox does: its watchdog treats the first mode with a 5-minute share-stall
  window, the second with its own 2-minute rule, and the third with a smart-plug
  power cycle. ([power-cycle guide](../power-cycle.md))

## A weak chip and the clock

One SC-BOX, 16 ICT580 chips on one board (`CPB0`). These facts belong to that
unit and its marginal chip, not the model.
`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`

- A marginal chip at the factory clock floods the board with bad nonces and the
  firmware resets the whole board (clock ramp 50 to 725 MHz, about 10 s of no
  hashing) every few seconds. Lowering to 600 MHz stopped it on this unit.
- That chip's bad-nonce share against clock: 8 to 10 percent at 600 MHz, 0.1
  percent at 575, none at 550 or 500, with zero resets at all four.
- Errors lag a clock change: after a drop from 600 to 575 MHz the chip kept
  producing bad nonces for about half an hour (2.9 percent in the first 29
  minutes), then settled to 0.1 percent over twelve hours. Judge a clock after an
  hour.
- The stock Miner page shows nothing wrong during any of this; the hidden
  `/#/debug` Chips view does.

([clock tuning guide](../clock-tuning.md))

## The hottest chip is in no API field

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-14]`

- `/dbg/icinfo` reports `temp` 0.0 for every chip, port 4028's `temp_max` is 0,
  and `tstemp-0` tracks the chip average plus about 3 C (identical to
  `tstemp-1` in 8,635 samples over three days, never above 75 C).
- The miner log's `MaxTemp` is the hottest chip. Over 15,333 lines and 17 boots
  it sat a fixed 11 C above `Avgtemp` (p5 7, p50 11, p95 16): the chip's
  position on the board, not a failed thermal interface, which shows 20 C or more
  and drifts.
- 90 C and above were 74 of 15,333 readings (0.5 percent), single 5-second
  samples on a level of 81 to 82; the top was 93. The firmware's overheat flag
  never set.
- A larger external fan and a 360 W supply dropped the average from 71 to 67 and
  the hottest chip from 82 to 78 or 79, more than any 25 MHz step on record.

## The miner log, `/dbg/minersyslog`

Plain text, one line per event, stamped `[YYYY-MM-DD HH:MM:SS]` by the miner's
clock. Evidence: [notebook](../firmware-api.md#the-miners-own-log-dbgminersyslog).

- The miner truncates it, and not at a restart. On 2026-09-20 one read returned
  3.8 MB reaching back 33 hours; 2.5 hours later a read returned 36 KB that began
  24 minutes earlier, with the mining process never having restarted. Lines about
  an incident are gone within hours. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]`
- A day is 2 to 3.5 MB and 31,000 to 51,000 lines; a 3.47 MB read took 0.8 s.
  It survives a power cycle. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-15]`
- It repeats the pool user in the start banner (`Pool 0 <url> user <user>`).
  Never store or print it. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-15]`
- A healthy SC-BOX writes `Chip Avgtemp` every 5 s (leading space, `C0` is the
  chain): ` [2026-09-15 12:00:00] C0: Chip Avgtemp 69.000000'C, MaxTemp 79.000000'C`
  (the time is a placeholder). Also `Work restart!`, `scanhash workid`,
  `Accepted`, `Hash Scan finished`, `detected new block`, and `Auto DTFS now
  check!!!` every 10 minutes.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-15]`

### The miner's clock

- It appears to have no real-time clock (inferred from the 2007 boot time).
  After a power loss it restarts at `2007-01-01 08:03` until the
  controller reaches a time server, and the log keeps the run before the outage:
  a `Started intminer` line stamped 2007 was followed by lines stamped
  2026-09-21 19:27. So the log is in timestamp order only within one run.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-21]`. On 2026-11-01 it again
  booted at 2007-01-01 08:00 until NTP set it.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- **Zone offset: UTC+8.** Once NTP has set it, the log's times run 13 hours
  ahead of EST on 2026-11-01 (19:10 on the log against 06:10) and ran 12 hours
  ahead of EDT in September. Both are UTC+8.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
  ([notebook](../firmware-api.md#2026-11-01-manual-plan-lost-across-a-power-cycle-sc-box))
  Use the stamps to order lines within one run; convert them before you say when.
- Blocking `find.goldshell.com` at the resolver is fine, but block only that
  name, because the clock needs NTP. ([off-LAN traffic](off-lan-traffic.md))

### Lines that mean something

Labels are the ones gbox's `classify_syslog` gives. `[verified: SC-BOX,
MCB_V5_4, fw 2.2.5, 2026-09-20]` for all rows.

| Line | Label | Means |
|---|---|---|
| `Started intminer ...` | `process_started` | the mining process started; nothing precedes it when it was killed or crashed |
| `SCBOX Init sucessed. 16 chips, N Total goodcores` | `init_succeeded` | a board init worked; `goodcores` is a running total, not a health figure |
| `Init failed 5 Times` | `init_failed` | the board would not initialize |
| `Write ChipN Reg N Failed` | `chip_write_failed` | the controller cannot reach that chip; at Chip0, the whole chain |
| `Read reg N error, exit.` | `reg_read_error` | same family |
| `BistStart err` | `bist_error` | the self-test would not start |
| `!!!SEND JOB FAILD 10 TIMES, REINIT THIS CPB!!!` | `sendjob_reinit` | a board reset (`rebootcnt` counts these) |
| `Read Nonce Faild 10 Times, Reinit Device!!!` | `readnonce_reinit` | a board reset, from the nonce side |
| `Auto addressing failed` | `addressing_failed` | no chip answered enumeration |
| `Set clk to NMHz Failed`, `actual nanMHz` | `set_clock_failed`, `clock_nan` | the PLL write failed |
| `Set TSENSOR_MODE Failed`, `Read TV_ACCESS MAX Failed` | `tsensor_failed` | the board's sensor chip did not answer |
| `WatchDog Exit for CPB Idle...` | `cpb_idle` | the firmware's idle watchdog; every 5 s while a board is absent |
| `gsbN devN: set device vfff, newval: V:MHz:..` | `settings_applied` | the firmware applied a voltage and clock |
| `INCS N: invalid nonce` | `invalid_nonce` | one bad nonce |
| `Pool N <url> not responding!`, `Stratum connection to pool N interrupted` | `pool_not_responding`, `stratum_interrupted` | the pool, not the board |
| `INCS N failure, exiting`, `gsbN_thread_shutdown` | `fatal_exit`, `thread_shutdown` | the mining process gave up |

During a restart the log shows a clock ramp, `ICT580 set Clock(req N MHz,
actual N MHz)`, from 50 MHz in 10 MHz steps. A poll mid-ramp reads a wandering
`clock`. On 2026-11-01 one poll read 670 MHz with `set_clock_failed` in the
log. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`

## Chip-level figures on this unit

- `goodcores` rises by 1277 at each successful board init on this unit. `[verified: SC-BOX, MCB_V5_4,
  fw 2.2.5, 2026-09-20]`
- On 2026-09-20 the unit ran 80 minutes at 162 W instead of 184 W on the same
  550 MHz with all 16 chips at normal rates, then stepped back up through one
  221 W sample. That is one instance of the two power levels below.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]`

## Two power levels at the same clock

([evidence](../firmware-api.md#2026-10-20-two-power-levels-at-one-clock-sc-box))

At one clock setting the SC-BOX runs at either of two power levels, about 10
to 20 W apart at the wall. The levels are repeatable but no setting selects
them, so the owner calls them "modes". In both levels these read the same:
clock, voltage, fan speed, hashrate, and all sixteen chips producing. The
board runs 3 to 4 C cooler in the low level, so the difference is real power,
not a meter artifact. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-20]`

The cleanest case: at 500 MHz and 0.41 V on the 360 W supply, 147 W became
161 W at a self-restart of the mining process on 2026-10-19, with fans at
1,216 against 1,212 rpm and hashrate 640 GH/s in both. Efficiency went from
230 to 252 J/TH. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-20]`

- Six instances are on record between 2026-09-14 and 2026-10-19, on two
  different supplies. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-20]`
- A restart can flip the level but is not required: one stretch began with no
  event, and a power cycle did not end another.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-21]`
- Nothing the firmware reports changes with the level. The voltage field is a
  setpoint echo ([port 4028](port-4028.md#voltage-on-the-sc-box-is-not-a-measurement)),
  so a core-voltage difference of a few tens of millivolts would fit the watts
  and the temperature but cannot be seen. `[unverified]`
- If you tune for efficiency, compare watts only within one level. A change
  that looks like 10 W saved may be a level flip.

**The cause is unknown, and the level may flip at any time ("mode
flipping").** Nothing the firmware exposes changes with it, and no
measurement available from outside the board has shown a way to find the
cause. Treat a step of 10 to 20 W at an unchanged clock as a possible flip
before you treat it as a fault or a tuning result. The owner keeps it as a
question of interest, to be analyzed again on demand, not as an open task.
`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`

Still unanswered: whether the levels differ in error rate over a long run, and
whether other BOX models do the same.

## What the unit does and does not survive

([power-cycle guide](../power-cycle.md))

- The board reset counter, hardware error counters and share counters start
  over at boot, so take increments between samples.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`
- Supply voltage is not measured by this firmware, see
  [port 4028](port-4028.md#voltage-on-the-sc-box-is-not-a-measurement).
