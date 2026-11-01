---
title: Goldshell BOX settings object and power plans
---

# Goldshell BOX settings object and power plans

`/mcb/setting`, the fields `manual` and `manualPowerplan`, the plan string
dialects, and what the stock UI writes when you press Save. This is the page
with the trap in it: the stock UI cannot set a tuned clock and silently undoes
one. Model and firmware context: the [matrix](README.md#model-and-firmware-matrix).
Evidence: [notebook, Settings object](../firmware-api.md#settings-object).

## The object

An SC-BOX answered `GET /mcb/setting` with this shape (the unit's MAC is in
`name`, shown here as a placeholder):
`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`

```json
{"select":0,"tempcontrol":true,"ledcontrol":true,
 "manualPowerplan":"725 MHz 0.41 V 70 RPM 70 RPM",
 "name":"<MAC>","powerplans":[{"level":0,"info":"725 MHz 0.41 V 70 RPM 70 RPM"},{"level":3,"info":"0 MHz 0 V 70 RPM 70 RPM"}],
 "manual":false,"temp_targets":[65.0,75.0],"version":"v2.1","temp_target":65}
```

- `PUT /mcb/setting` takes the whole object back, with your changes.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` A partial body is said to
  drop fields because the endpoints replace the object, `[unverified]` for
  `/mcb/setting` itself (the note comes from the WiFi tests).
- Each settings PUT restarts the fan daemon, which spikes the fans for a few
  minutes before the PID settles. Do not read that spike as a result.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]`

## `manual` and `manualPowerplan`

- With `manual:false`, the unit runs the selected preset (`select` picks it
  from `powerplans`). On the SC-BOX that is one real preset, 725 MHz at 0.41 V
  (level 0), plus a 0 MHz entry that means "off" (level 3).
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- With `manual:true`, the unit runs the string in `manualPowerplan`, live, with
  no reboot. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- The stock UI never shows `manual`. The Miner page keeps showing "Hashrate
  Mode" while a manual plan runs. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- The fan fields inside the plan string are ignored. Fan speed is a PID on the
  board sensor (`tstemp-2`) toward `temp_target`.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-07]`

### What the stock UI Save writes

Read from the stock UI's `setting-miner` chunk on 2026-09-07, and confirmed on
2026-11-01: `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`

- The Miner page has one settings block: miner name (read-only), a power plan
  dropdown over `powerplans`, a temperature control checkbox (read-only) and a
  fan target slider bounded by `temp_targets`.
- Its Save handler sets `manual` to the inverse of a "power plan shown" flag
  that starts true and never changes, then PUTs the object. So **every Save
  writes `manual:false`**, whichever field you touched. A tuned clock silently
  reverts to the preset, even if you only moved the fan target.
- The manual plan field in the UI is dead code. The stock UI cannot set any
  clock other than the presets.
- Pools (`/mcb/newpool`, `/mcb/delpool`), algorithm (`/mcb/algosetting`) and
  RGB (`/mcb/rgbsetting`) on the same page use their own endpoints and do not
  touch `manual`. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-07]`
- What gbox does about it: it writes the whole object with `manual:true` and
  changes only the `MHz` token, and its clock tuning guide warns not to press
  Save on the Miner page. ([clock tuning](../clock-tuning.md))

## Does a manual plan survive a power cycle?

`[contradicted: 2026-11-01, see this section]`

- Earlier notes said the manual plan survives a power cycle. That held for more
  than 20 cold boots between 2026-10-20 and 2026-10-31.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-31]`
- On 2026-11-01 at 06:09 EST, after a controller hang (wall draw dropped to
  about 12 W at 05:57) and a 120 s power cut, the unit came back with
  `manual:false`, preset 0, 725 MHz, read with `gbox status`. Voltage and fan
  target came through unchanged.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- The cause is unknown. This is one observation, not a rule. Do not assume
  either outcome: check the clock after every power loss.
- The same fact in two older pages is also marked: the
  [notebook](../firmware-api.md) paragraph on the third failure mode and the
  [power-cycle guide](../power-cycle.md).

([evidence](../firmware-api.md#hashboard-behavior-seen-on-the-sc-box))

## Clock readings during chip-init failures

- During chip-init failures the clock can read an intermediate value for one
  poll: 670 MHz at 07:06 on 2026-11-01, with `set_clock_failed` in the miner
  log. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-11-01]`
- A restart ramps the clock from 50 MHz in 10 MHz steps, so a poll can read
  280, 410, 80, 300. That is a board being re-initialised, not retuned.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]`

## Temperature and fan fields

- `temp_target` is clamped by the fan daemon to `temp_targets` (65 to 75 on the
  SC-BOX, 70 to 80 on the HS-BOX). `temp_targets` is read-only. 65 is the
  coolest available on the SC-BOX.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`;
  `[verified: HS-BOX, MCB_V5_4, fw 2.2.6, 2026-09-25]`
- The board sensor reads 5 to 10 C below the chips.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- `tempcontrol` is the overheat-shutdown flag, and it does not affect fans.
  With it set false, an SC Lite's fan log kept writing `Fans Change` and
  `Fan stay` lines with `target_temp:85`, as with the flag on (first about 60 s,
  then 10 minutes with an abort at 75 C that was never reached).
  `[reported: SC Lite owner, MCB_V4_3, fw 2.2.0, 2026-09-22]`
- What `tempcontrol:false` does under heat is `[unverified]` on every model.
  gbox never writes it.
- `temp_target` lives in `/mcb/setting`, not in `/dbg/minerinfo`. Reading it
  every poll (three requests per 30 s cycle) caused no trouble.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`

## Power plan dialects

The plan string differs by model. gbox parses all three from the string, not
from a model table, and rewrites only the `MHz` token.

| Dialect | Example | Where | Tag |
|---|---|---|---|
| `box` | `575 MHz 0.41 V 90 RPM 90 RPM` | SC-BOX; HS-BOX writes the same form, e.g. `850 MHz 0.44 V 50 RPM 50 RPM` (Blake2B) and `750 MHz 0.41 V 50 RPM 50 RPM` (Handshake), no `PV` term | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`; `[verified: HS-BOX, MCB_V5_4, fw 2.2.6, 2026-09-25]` |
| `mv_pv` | `625 MHz 9100 V 40 RPM 40 RPM PV 9400` | SC Lite: integer volts (their notes say millivolts), then a trailing `PV` term. `/dbg/minerhistory` shows the internal form `intchains_qomo:vfff=<pv>:<MHz>:<fanA>:<fanB>:<mV>` | `[reported: SC Lite owner, MCB_V4_3, fw 2.2.0, 2026-09-22]` |
| `float_pv` | `750 MHz 0.41 V 50 RPM 50 RPM PV 9400` | the "float-V / optional-PV" form the same notes give for the HS Box | `[unverified]`: no verbatim example with a PV term is on record |

- Nobody has confirmed what the SC Lite's integer volts or the `PV` term mean.
  `[unverified]`
  ([evidence](../firmware-api.md#power-plan-dialects))

## The HS-BOX nests its presets per algorithm

`[verified: HS-BOX, MCB_V5_4, fw 2.2.6, 2026-09-25]`

- The HS-BOX mines two algorithms. Its `/mcb/setting` carries `algoname` (the
  running one, `blake2b(SC)` that day) and a `powerplans` list of
  `{algo, mode: [{level, info}]}`, one entry per algorithm, Handshake first.
- Every other model on record has a flat `[{level, info}]` list.
  `[reported: SC Lite owner, MCB_V4_3, fw 2.2.0, 2026-09-22]` for the SC Lite;
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- On that unit `manualPowerplan` held the Handshake plan while the unit ran the
  Blake2B preset (`manual` false, `select` 0). So reading `manualPowerplan` as
  "the plan that is running" is wrong on an HS-BOX.
- `temp_targets` (`[70, 80]`) and `temp_target` are flat, as on the SC-BOX.
- What gbox does about it: its clock control reads only the flat shape, so on
  an HS-BOX it would find no presets, fall back to a 725 MHz ceiling below the
  unit's 850 MHz preset, and start from the wrong plan. Do not use gbox's clock
  control on an HS-BOX until it reads the nested shape.

## How an SC-BOX behaves at each clock

These belong to one unit, not the model. They are in
[behaviour and failure modes](behaviour-and-failure-modes.md#a-weak-chip-and-the-clock).
