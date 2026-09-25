# Goldshell Box firmware API notes

Learned on 2026-09-05 from an SC-BOX, hardware 40.40.HA, controller
MCB_V5_4, firmware 2.2.5 (web UI titled "cloud-box"). Other Box-series
models ship the same UI and are expected to match; confirmations welcome.
No Goldshell code is reproduced here, only observed behavior.

## Authentication

- `GET /user/login?username=admin&password=<hex>&cipher=true`
- `<hex>` is AES-128-CBC of the UTF-8 password, key `!!!!!!!!!!!!!!!!`
  (sixteen `!`), zero IV, **zero padding** (not PKCS#7), ciphertext as hex.
  WebCrypto only does PKCS#7: encrypt the zero-padded text and drop the final
  block, the CBC chain is identical up to there.
- Response: `{"JWT Token": "<RS256 JWT>"}`. The payload has no expiry and no
  nonce, so every login returns the identical token and it never expires.
  It is the same token on every unit: the payload is fixed and RS256 is
  deterministic, and on 2026-09-15 the SC-BOX's token matched an SC5 Pro II's
  on every one of 458 compared characters (payload: audience "yotta mc",
  issuer intchains.com, id 1, subject "minerd"). So the web password gates
  nothing the miner does over HTTP: anyone on the network holding any
  Goldshell's token, and the helper repos on GitHub carry it, can read
  settings, change pools or restart the unit. Treat the miner as an
  unauthenticated device on a trusted LAN; gbox's own password prompt on
  the power buttons guards the page, not the miner.
- Every other request: header `Authorization: Bearer <token>`.
- CORS: `Access-Control-Allow-Origin: *`. A preflight `OPTIONS` on
  `/mcb/setting` and `/mcb/restart` answers 200 with
  `Access-Control-Allow-Methods: GET, PUT, POST, OPTIONS` and
  `Access-Control-Allow-Headers: appkey,X_forwarded-for,Content-Type,Authorization, X-Requested-With`
  (checked 2026-09-06). So a page opened from disk or another origin can PUT
  settings and restart the miner, which is what the dashboard's buttons do.

## Endpoints (all need the token except `/mcb/status`)

Every path below needs the token except `GET /mcb/status`, which answers 200 with
no `Authorization` header at all: one `curl` between two service polls returned
`{"hardware":"40.40.HA","model":"Goldshell-SCBox","mcbversion":"MCB_V5_4","firmware":"2.2.5"}`
(verified on the SC-BOX, 2026-09-19; **unverified on the SC5 Pro II**). That is what
`gbox discover` probes with, so a sweep reads no credential and sends none. A *wrong*
token on that path is untested either way.

| Path | Method | Notes |
|---|---|---|
| `/mcb/status` | GET | model, hardware, mcbversion, firmware |
| `/mcb/setting` | GET/PUT | see Settings below |
| `/mcb/pools` | GET/PUT | pool list |
| `/mcb/newpool`, `/mcb/delpool` | PUT | |
| `/mcb/restart` | PUT, no body | soft restart (full reboot of the controller) |
| `/mcb/facrst` | PUT | factory reset, never call from tooling |
| `/mcb/algosetting`, `/mcb/cmossetting`, `/mcb/rgbsetting`, `/mcb/ip`, `/mcb/wifisetting` | GET/PUT | |
| `/mcb/cgminer` | GET | returned 500 on this unit |
| `/mcb/protect`, `/mcb/defend` | PUT, no body | in the stock UI's API module (2026-09-07); purpose unknown, never called |
| `/mcb/uploadimage` | POST | firmware upload; never called |
| `/mcb/tutorial`, `/mcb/resultpool`, `/mcb/wifiresult` | GET | stock UI helpers |
| `/cpb/hshistory` | GET | JSON array, 288 samples, one per minute, MH/s, newest last, zeros before first sample |
| `/dbg/minerinfo` | GET | cgminer-style text: a `[STATUS]` block, then one `[PGAn] =>` block per hashboard (one on the SC-BOX; four on the SC5 Pro II). The SC5 Pro II's `[STATUS]` block carries `voltage` (mV), `current` (unit-level, the same value repeated on every board, mA by the arithmetic against the 3300 W rating, unverified) and `powerid`. The SC-BOX's `[STATUS]` block carries none of the three, so this transport yields no firmware watts figure on that unit. Answered 200 on an SC5 Pro II (MCB_V3_3, fw 2.2.0) on 2026-09-15 with and without a Referer, so the "debug lock" the SC Lite notes describe is not on that firmware. It answered 200 on an SC Lite (MCB_V4_3, fw 2.2.0) on 2026-09-22 too, with four `[PGAn]` blocks; both `Authorization: <token>` and `Authorization: Bearer <token>` worked there. Keys used: `Device Elapsed`, `MHS av`, `MHS 20s`, `Accepted`, `Rejected`, `Hardware Errors`, `Device Hardware%`, `clock`, `fan0`, `fan1`, `tstemp-0` (chip temperature: the board's chip average plus about 3 C on the SC-BOX, never the hottest chip; see the hashboard section), `tstemp-2` (board sensor), `rebootcnt`, `overheat` |
| `/dbg/icinfo` | GET | JSON `{body: "<json string>"}`; `drawdata` is an array of boards, each an array of chips with `chipindex`, `perf` (good nonces), `hwerr` (bad nonces). Chips are numbered from 1. Since 0.6.0 the service logs every chip's counts each poll (`chips` column, `board.chip:good/bad`); the counters restart at every board reinit, so per-chip rates are sums of increments |
| `/dbg/fanctrllog` | GET | fan daemon log, grows to ~1 MB; lines like `Fans Change (fan0: 62 ==> 61) ... reason(t:64.2 acc:0.0 target_temp:65)` |
| `/dbg/minersyslog` | GET | cgminer log as text, 2 to 3.5 MB and 31k to 51k lines for a day (a 3.47 MB read on 2026-09-15 took 0.8 s). It survives a power cycle. The only place the hottest chip's temperature is reported: one line every 5 s, ` [YYYY-MM-DD HH:MM:SS] C0: Chip Avgtemp 69.000000'C, MaxTemp 79.000000'C` (leading space; `C0` is the chain, and no line anywhere in the log names a chip's temperature, so which chip is hottest is never reported; the miner's clock ran 12 h ahead of local time on this unit, so use its timestamps for ordering only). Also carries the `SCBOX Init sucessed. 16 chips` boot lines and `Write Chip0 Reg 4 Failed`. It repeats the pool user, so it must never be persisted or logged; `gbox serve` reads it every `syslog_interval` seconds (300 by default) and keeps three numbers from it |
| `/dbg/syslog` | GET | web backend log; `Check Token Error`, `Minerd start !!!`, `Restart Miner` live here |
| `/dbg/kmsg`, `/dbg/meminfo`, `/dbg/psinfo`, `/dbg/monitorlog`, `/dbg/minerhistory` | GET | |
| `/dbg/vsinfo` | GET | 500 on this unit |

The hidden page `/#/debug` in the stock UI renders most of the `/dbg/` data.

## Port 4028 (no token)

cgminer's classic socket API, `intminer 5.4.2`, answers on TCP 4028 with no
token on the SC-BOX and on an SC5 Pro II (both checked 2026-09-15), and on an
SC Lite (2026-09-22; four boards, `voltage` and `powerid` but no `current`). Send one
JSON object, read until the miner closes the connection; the reply ends in
one NUL byte.

| command | answer |
|---|---|
| `{"command":"devs"}` | `DEVS`: one object per hashboard with the same keys as the `[PGAn]` blocks of `/dbg/minerinfo` (`Device Elapsed`, `MHS av`, `MHS 20s`, `Nonced`, `Accepted`, `Rejected`, `Hardware Errors`, `Device Hardware%`, `clock`, `fan0..fanN`, `tstemp-0/1/2`, `overheat`, `rebootcnt`, `hwerr-ration`; plus `voltage`, `current` and `powerid` on the SC5 Pro II). Valid JSON. The SC-BOX's 4028 `devs` carries a `voltage` of its own, read as 0.41 on 2026-09-15, in an undocumented unit that is plainly not the SC5 Pro II's millivolts; it carries no `current` and no `powerid`, and its `/dbg/minerinfo` carries none of the three, so no firmware watts figure is available on that unit by either transport. The values matched the HTTP read within a poll |
| `{"command":"summary"}` | unit totals: `Elapsed`, `MHS av`, `MHS 20s`, `Accepted`, `Rejected`, `Hardware Errors`, `Device Hardware%`, pool percentages. No temperatures or fans |
| `{"command":"stats"}` | malformed JSON (objects run together, `}{`); `temp_max` reads 0. Not used |
| `{"command":"pools"}` | the pool URL and user. Never sent by gbox |

It bypasses the web backend, so it has neither the token race nor the burst
crash below, and it needs no login. A browser cannot open it, so the page
still reads `/dbg/minerinfo`; the service reads 4028 first (gate 2, 0.8.0).

## Settings object

```json
{"select":0,"tempcontrol":true,"ledcontrol":true,
 "manualPowerplan":"725 MHz 0.41 V 70 RPM 70 RPM",
 "name":"<MAC>","powerplans":[{"level":0,"info":"725 MHz 0.41 V 70 RPM 70 RPM"},{"level":3,"info":"0 MHz 0 V 70 RPM 70 RPM"}],
 "manual":false,"temp_targets":[65.0,75.0],"version":"v2.1","temp_target":65}
```

- PUT the whole object back with changes. `manual:true` + a new
  `manualPowerplan` string changes the clock live, no reboot.
- **The fan fields in the plan string are ignored.** Fan speed is a PID on
  the board sensor (`tstemp-2`, the temperature the stock UI shows) toward
  `temp_target`. The board sensor reads 5-10 C below the chips.
- `temp_target` is clamped by the fan daemon to `temp_targets` (65-75 on this
  unit); `temp_targets` itself is read-only. 65 is the coolest available.
- `tempcontrol` is the overheat-shutdown flag; it does not affect fans. The
  same held on MaVeTh's SC Lite (fw 2.2.0) on 2026-09-22: with the flag set
  false (confirmed by a GET) first for about 60 s, then for 10 minutes with an
  abort at 75 C (never reached; 70 C peak), its fan log kept writing `Fans
  Change` and `Fan stay` lines with `target_temp:85` and the 4028 fan speeds
  kept walking, as with the flag on. What the flag does under heat is
  untested on either model, so gbox never writes it.
- Each settings PUT restarts the fan daemon, which spikes the fans for a few
  minutes before the PID settles again. Do not read that spike as a result.
- **The stock Miner page** (read from its `setting-miner` chunk, 2026-09-07)
  has one settings block: miner name (read-only), power plan dropdown over
  `powerplans`, temperature control checkbox (read-only), and a fan target
  slider bounded by `temp_targets` (shown when `temp_targets[0] > 0`). Its
  Save handler sets `manual` to the inverse of a "power plan shown" flag that
  is initialized true and never changed, then PUTs the object. So every Save
  from that block writes `manual: false`, whichever field was touched, and a
  manual clock silently reverts to the preset. The manual power-plan field
  itself is dead code. Pools (`/mcb/newpool`, `/mcb/delpool`), algorithm
  (`/mcb/algosetting`) and RGB (`/mcb/rgbsetting`) on the same page use their
  own endpoints and do not touch `manual`.

## Power plan dialects

The plan string differs by model. Seen or documented so far:

| Dialect | Example | Where |
|---|---|---|
| `box` | `575 MHz 0.41 V 90 RPM 90 RPM` | SC-BOX, read from the unit. The HS-BOX writes the same form, read from the maintainer's unit (MCB_V5_4, fw 2.2.6) on 2026-09-25: `850 MHz 0.44 V 50 RPM 50 RPM` for Blake2B (Sia), `750 MHz 0.41 V 50 RPM 50 RPM` for Handshake, no `PV` term |
| `mv_pv` | `625 MHz 9100 V 40 RPM 40 RPM PV 9400` | SC Lite, firmware 2.2.0, from Maveth/goldshell-config, and read from his unit's `/mcb/setting` on 2026-09-22. The volts field is an integer (their notes call it millivolts) and a trailing `PV` term follows. `/dbg/minerhistory` shows the internal form `intchains_qomo:vfff=<pv>:<MHz>:<fanA>:<fanB>:<mV>` |
| `float_pv` | `750 MHz 0.41 V 50 RPM 50 RPM PV 9400` | the "float-V / optional-PV" form the same notes give for the HS Box; no verbatim example with a PV term is on record |

gbox parses all three from the string itself, not from the model table, and
the clock control rewrites only the `MHz` token: every other token goes
back exactly as the firmware wrote it (`with_mhz` in `gbox/api.py`,
`withMhz` in `app.js`). Nobody has confirmed what the SC Lite's integer
volts or the `PV` term mean, and nothing in gbox needs to know; the
confirm dialog shows the exact string either way.

**The HS-BOX nests its presets per algorithm.** It mines two algorithms, and
its `/mcb/setting` carries `algoname` (the running one, `blake2b(SC)` on
2026-09-25) and a `powerplans` list of `{algo, mode: [{level, info}]}`, one
entry per algorithm, Handshake first. Every other model on record has a flat
`[{level, info}]` list, and the clock code reads only that shape
(`max_preset_mhz` and `set_plan` in `gbox/api.py`, `presetPlan`,
`clockRange` and `presetList` in `app.js`). Against this unit it would
find no presets, fall back to a 725 MHz ceiling below the unit's own 850 MHz
preset, and start a clock change from `manualPowerplan`, which held the
Handshake plan while the unit ran the Sia preset (`manual` false, `select`
0). **Do not use gbox's clock control on an HS-BOX until the nested shape is
read and the plan is taken from the running algorithm.** The fan target is
unaffected: `temp_targets` (`[70, 80]`) and `temp_target` are flat, as on
the SC-BOX.

## Reliability quirks

- The token check has a race: with several requests in flight, roughly one in
  200 gets `401` and the syslog shows `Check Token Error` preceded by a failed
  reload of `pubkey.pem`. Send requests one at a time; retry a 401 before
  believing it.
- Request bursts of roughly 15 per second crash the web backend (`Minerd
  start !!!` in syslog). It restarts on its own and loses the hashrate history
  buffer. Poll gently: one cycle per 10 s, slow endpoints once a minute.
- `/dbg/` files are regenerated per request; `dbg/fanctrllog` occasionally
  returns a length mismatch mid-write. Keep the last good value.

## Counters, and what they can and cannot tell you

Verified 2026-09-08 on the SC-BOX, from the gbox log:

- `Hardware Errors` in `minerinfo` equals the sum of `hwerr` over every chip
  in `icinfo` (1711 = chip 8's bad count when it was the only chip with
  any). `Device Hardware%` is that count over all nonces since the miner
  started: a running average that drifts for hours after a change. For the
  error rate of one clock, take the difference of `Hardware Errors` and of
  the summed `perf` between two samples.
- `Device Elapsed`, `Accepted`, `Rejected`, `Hardware Errors` and
  `rebootcnt` all reset to zero on a controller restart (soft restart, power
  cycle, or the watchdog). A drop in `Device Elapsed` is the reliable sign
  of one.
- `Accepted` can also reset without a restart when the pool connection is
  re-established, and shares per hour follow the pool's per-connection
  difficulty as much as the miner: the same unit logged 1521 and 643
  accepted shares per hour at the same clock and hashrate on two pool
  sessions. Use `MHS 20s` or the summed `perf` for throughput.
- `temp_target` lives in `mcb/setting`, not in `minerinfo`; reading it every
  poll (three requests per 30 s cycle) has caused no trouble.

## Hashboard behavior seen on the SC-BOX

- 16 ICT580 chips on one board (`CPB0`). Healthy chips return good nonces at
  roughly equal rates with single-digit bad nonces.
- A marginal chip at the factory clock floods the board with bad nonces; the
  firmware answers `Read Nonce Faild 10 Times, Reinit Device!!!` and resets
  the whole board (clock ramp 50 -> 725 MHz, ~10 s of no hashing) every few
  seconds. `rebootcnt` in `minerinfo` counts these. Lowering the clock to
  600 MHz stopped it entirely on this unit.
- The same chip's bad-nonce share is a steep function of clock: 8 to 10
  percent at 600 MHz, 0.1 percent at 575 MHz, and none at 550 or 500 MHz, with
  zero board resets at all four. Measured over days at 600 and 575, and
  reproduced in 34 minutes when `gbox trials run` stepped the unit back up
  to 600 on 2026-09-08 (8.5 percent, guard tripped).
- Errors lag a clock change. After the drop from 600 back to 575 MHz the
  chip kept producing bad nonces for about half an hour (2.9 percent of its
  nonces in the first 29 minutes), then settled to 0.1 percent over the
  next twelve hours. Judge a clock after an hour, not after ten minutes.
- The hottest chip is not in any API field. `/dbg/icinfo` reports `temp` 0.0
  for every chip, port 4028's `temp_max` is 0, and `tstemp-0` tracks the
  chip average plus about 3 C (identical to `tstemp-1` in 8,635 samples over
  three days, never above 75 C). The cgminer log's `MaxTemp` is the hottest
  chip. Over 15,333 log lines and 17 boots on 2026-09-13 and 14 it sat a
  fixed 11 C above `Avgtemp` (p5 7, p50 11, p95 16) at steady state and
  right after a boot, at 550 and 575 MHz, on both power supplies: the
  position on the board, not a failed thermal interface, which shows 20 C
  or more and drifts. Readings of 90 C and above were 74 of 15,333 (0.5
  percent), single 5-second samples on a level of 81 to 82 with p95 at 86;
  the top was 93. The firmware's overheat flag never set. A larger external
  fan and a 360 W supply dropped the average from 71 to 67 and the hottest
  chip from 82 to 78 or 79, more than any 25 MHz step on record. Since
  0.7.0 the service logs the hottest chip's 5-minute median (`hot_level`),
  its peak (`hot_peak`) and the chip average (`chip_avg`), and the flags sit
  on the level, not the peak.
- The fan controller steers on the board sensor, so a small drop there buys
  a large fan-speed drop. An external fan pulling air off the outlet side
  lowered the board sensor 2.4 C (64.7 to 62.3) and the internal fans fell
  from 1860 to 1200 RPM and stayed there. No measurable change in error
  rates either way.
- A second failure mode: the board accepts work and reports each job finished
  instantly without hashing; the firmware notices after minutes via
  `SEND JOB FAILD 10 TIMES, REINIT THIS CPB`.
- A third, in the controller rather than the board: the unit drops off the
  network entirely (no HTTP, no ping, no ARP) with the fans still turning.
  Seen 2026-09-06, 2026-09-09 19:00 and 2026-09-09 22:22, the first two
  after days of clean hashing at a manual clock, the third 34 minutes
  after the power cycle that cleared the second. A soft restart cannot
  reach it, and the manual power plan survives a power cycle. At the wall
  it drew about 53 W while hung on a plug-in meter, and a steady 33.5 to
  34 W on a Kasa HS110's meter during the third episode, against 183 to
  188 W hashing; it blew barely warm air: the hashboard is idle and only
  the controller is running. The third episode ended on its own after 51
  minutes: the web backend answered an HTTP 500 during boot, the meter
  went from 34 W straight to 188 W, and the uptime counter restarted. A
  plug-in power meter tells the state from across the room, and the
  smart-plug rung of the watchdog (`docs/power-cycle.md`) is the
  automatic version of the hand on the plug.
- Two deliberate power cycles of a healthy unit (2026-09-10, 15 s off,
  through the HS110): the web backend answered again 60 and 66 seconds
  after the cut, at the manual clock, `rebootcnt` 0 and `Device Elapsed`
  under 30 s on the first sample. While the controller booted the plug read
  38 to 41 W and one poll got an HTTP 500, the same signature as the
  self-recovery above. Power was back to 189 to 190 W on the first good
  sample. The 2 to 3 minutes the guide quotes is the conservative figure;
  a minute is what this unit does.

## The miner's own log, `/dbg/minersyslog`

Plain text, one line per event, each stamped `[YYYY-MM-DD HH:MM:SS]` by the
miner's clock. Three things about it are easy to get wrong.

- **The miner truncates it, and not at a restart.** On 2026-09-20 one read
  returned 3.8 MB reaching back 33 hours; a read 2.5 hours later returned
  36 KB that began 24 minutes earlier, with the mining process never having
  restarted in between. Lines about an incident are gone within hours. Since
  0.9.0 the service keeps a summary (`minerlog.csv`, below).
- **Its clock is not yours.** On this SC-BOX it ran 12 hours ahead of local
  time. The stamps are good for ordering lines and for nothing else.
- **Not even for ordering across a cold boot.** After a power loss the clock
  restarts at `2007-01-01 08:03` until the controller reaches a time server,
  and the log keeps the run before the outage (2026-09-21 09:44: a
  `Started intminer` line stamped 2007 followed lines stamped 2026-09-21
  19:27). So the log is in timestamp order only within one run. Since 0.9.1
  the service places its cursor by position in the log, and takes the newest
  `Init sucessed` or `Started intminer` line, by position, as the start of
  the current run.
- **It repeats the pool user** in the start banner (`Pool 0 <url> user
  <user>`). Nothing that reads it may store or print its text.

What a healthy SC-BOX writes all day: `Chip Avgtemp` every 5 s, `Work
restart!`, `scanhash workid`, the `=======>` line, `Accepted`, `Hash Scan
finished`, `detected new block`, `Auto DTFS now check!!!` every 10 minutes,
and at a start the banner, the pool lines and a clock ramp of `ICT580 set
Clock(req N MHz, actual N MHz)` from 50 MHz up in 10 MHz steps. Read once a
poll, that ramp makes the `clock` field appear to wander (280, 410, 80, 300
on 2026-09-20); it is a board being re-initialised, not retuned.

The lines that mean something, and the label `classify_syslog` gives each:

| line | label | seen when |
|---|---|---|
| `Started intminer ...` | `process_started` | the mining process started; nothing precedes it when it was killed or crashed |
| `SCBOX Init sucessed. 16 chips, N Total goodcores` | `init_succeeded` | a board init worked. `goodcores` is a running total (+1277 a time on this unit), not a health figure |
| `Init failed 5 Times` | `init_failed` | the board would not initialise |
| `Write ChipN Reg N Failed` | `chip_write_failed` | the controller cannot reach that chip; at Chip0 it cannot reach the chain at all |
| `Read reg N error, exit.` | `reg_read_error` | same family |
| `BistStart err` | `bist_error` | the self-test would not start |
| `!!!SEND JOB FAILD 10 TIMES, REINIT THIS CPB!!!` | `sendjob_reinit` | a board reset (`rebootcnt` counts these) |
| `Read Nonce Faild 10 Times, Reinit Device!!!` | `readnonce_reinit` | a board reset, from the nonce side |
| `Auto addressing failed` | `addressing_failed` | no chip answered enumeration |
| `Set clk to NMHz Failed`, `actual nanMHz` | `set_clock_failed`, `clock_nan` | the PLL write failed |
| `Set TSENSOR_MODE Failed`, `Read TV_ACCESS MAX Failed` | `tsensor_failed` | the board's sensor chip did not answer |
| `WatchDog Exit for CPB Idle...` | `cpb_idle` | the firmware's own idle watchdog; every 5 s while a board is absent |
| `gsbN devN: set device vfff, newval: V:MHz:..` | `settings_applied` | the firmware applied a voltage and clock |
| `INCS N: invalid nonce` | `invalid_nonce` | one bad nonce |
| `Pool N <url> not responding!`, `Stratum connection to pool N interrupted` | `pool_not_responding`, `stratum_interrupted` | the pool, not the board |
| `INCS N failure, exiting`, `gsbN_thread_shutdown` | `fatal_exit`, `thread_shutdown` | the mining process gave up (2026-09-20, nine minutes after a start that found no chips) |

Anything else is counted as `other` and its text is dropped.

### Two ways a sick board looks, and they behave oppositely

From 15 days of one SC-BOX's service log (2026-09-05 to 09-20):

| | what `/dbg/minerinfo` or port 4028 shows | episodes | how each ended |
|---|---|---|---|
| reset burst | `rebootcnt` climbing by 8 or more a sample, `clock` and temperatures real (often caught mid-ramp) | 12 | all by themselves, in 0 to 2.5 minutes |
| board absent | HTTP fine, `clock` 0, `tstemp-2` at -150 (the no-sensor value), `rebootcnt` static, a few watts at the wall | 6 | none by itself; 6 to 22 minutes, each until a soft restart |

The watchdog's 5-minute share-stall window is right for the first and wasted
on the second, which since 0.9.0 has its own 2-minute rule
(`watchdog.absent_minutes`). A soft restart is not free on this unit: of the
four sent while the board was still present, three lost the board and needed
another. Of the 13 sent to a board already absent or a miner already
unreachable, none left it in a worse state than it found it.

### The voltage field

`voltage` in port 4028's `devs` reads 0.41 on the SC-BOX, the same number
the plan string carries (`550 MHz 0.41 V ...`) and the log prints as
`tvout:0.410000` when the setting is applied. **On the SC-BOX it is the
setpoint echoed, not a measurement:** on 2026-09-21 all 1778 logged rows read
exactly 0.41, through 162 W, 184 W and 221 W stretches at the same clock and
through samples under 50 W. (Not yet checked during a board-absent episode,
which would close it.) So nothing the SC-BOX firmware reports measures supply
voltage. On the SC5 Pro II it is millivolts at the supply (11750). On the SC
Lite (2026-09-22) it read 9330 on all four boards across five reads a minute
apart while the plan said 9100, so there it is not the setpoint; whether it
moves with load is unobserved. Since 0.9.0 the service logs it unconverted as
`volts`. The reason: on 2026-09-20 the SC-BOX ran 80
minutes at 162 W instead of 184 W on the same 550 MHz with all 16 chips
producing at their normal rates, then stepped back up through a single
221 W sample, and nothing on record could say whether the voltage had moved.

### Silent supervisor restarts

The web backend's own log (`/dbg/syslog`) writes `Minerd start !!!` whenever
the supervisor process comes up, and `Restart Miner` just before it when a
restart was requested over HTTP. On the SC-BOX on 2026-09-21 two restarts
(18:36 and 21:55 local) had `Minerd start` with no `Restart Miner` before
them, no power loss (the clock did not fall back to 2007), and a pause of
about 11 s in the backend's own 3-second `[cmos]` lines. Nothing in either
log names the cause. It also shows that a restart request which timed out on
the client (18:32 that evening) never reached the miner. The backend does
not log `/dbg/minerinfo` or `/dbg/icinfo` reads, so its silence does not
rule out a read arriving at the same moment. Every fan burst to about
4,300 RPM that day was one of these restarts, or a boot after a power cut:
the fans run at full speed while the miner boots.

The same day's hangs, where port 4028 and HTTP both stop answering until a
power cut, sit among runs of `Auto addressing failed`, `BistStart err`,
`SEND JOB FAILD ... REINIT THIS CPB` and `WatchDog Exit for CPB Idle` in the
miner's log: the controller losing its hashboard (CPB). Three more such
episodes that evening recovered by themselves within two minutes.

## What the firmware sends off the LAN

The SC-BOX's web backend posts to `find.goldshell.com/api/miners`,
apparently for Goldshell's find-my-miner feature. Each post carries the model,
serial number, MAC and LAN address, and the server sees the home's public
address from the connection. Its log showed 41 posts over about a day, 22 of
them answered `{"status":"ok"}`. No pool, wallet or hashrate was in them. It
is not needed for mining; an owner who wants it stopped can block that one
name at the resolver. Block only that name: the miner sets its clock over
NTP, and a cold start reads 2007 until it does. Whether other models do the
same is unchecked.
