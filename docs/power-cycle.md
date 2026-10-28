# Power-cycling a hung miner through a smart plug

The software watchdog sends a soft restart when the miner stalls or drops
off the network. A frozen controller cannot take one: no HTTP, no ping,
the restart request times out, and the hashboard sits idle at about 34 W
until someone pulls the plug. Three such freezes are on record for one
SC-BOX (2026-09-06, and twice on 2026-09-09); the owner cleared two by
hand, and one cleared itself after 51 minutes.

The power rung does what the hand does. It is optional, off until you set
it up, and in **dry run** until you arm it: for as long as you like, the
event log says what it would have done and nothing moves.

## Which plugs

The service talks to the plug directly on the LAN; no cloud, no app, no
account. This version drives TP-Link Kasa plugs on their original local
protocol (the one the Kasa app used before 2023; port 9999, no
authentication). The list below is the same one the dashboard shows under
the watts chart; both come from python-kasa's supported-device list and
TP-Link's own listing, checked 2026-09-10.

- **Meter, original protocol, works today:** KP115, KP125, and the
  end-of-life HS110 (the unit this was built against).
- **Meter, but the firmware requires the TP-Link account (KLAP), not
  driven yet:** KP125M (the Matter one), EP25.
- **Meter per outlet, six-outlet strip, not supported here:** HS300.
- **No meter, switch only** (fine for power-cycling; the service shows
  the relay state and the watts chart stays empty): HS100 (end of life),
  HS103, HS105, KP100, KP105, KP401, EP10; the outdoor EP40, EP40A and
  EP40M; the strips HS107, KP200, KP303, KP400. The HS100 family has no
  meter on any hardware version.

TP-Link's listing: <https://www.kasasmart.com/us/products/smart-plugs>;
the three marked "Slim with Energy Monitoring" there are the metering
ones (KP125M, EP25, KP125).

Plugs whose firmware moved to the newer encrypted protocol (KLAP: Tapo
plugs, and Kasa units updated in 2023 to 2025) answer `gbox power
discover` but cannot be driven yet; they need the TP-Link account
credentials. Shelly and Tasmota plugs are the planned next drivers.

**Relay ratings.** A 15 A plug at 120 V is fine for a Box-class miner at
200 W. It is not the right switch for a 240 V, 3 kW ASIC, and switching a
large power supply's inrush through a consumer relay risks welding it
shut. Over about 1.5 kW use a Shelly Pro 4PM, a Tasmota-flashed Sonoff
POWR3, or a switched rack PDU, once those drivers exist.

## Setting up

Find the plug. Discovery broadcasts on the LAN and lists what answers;
Windows may ask once to let Python receive on the network:

```
gbox power discover
address          model        name                     relay meter     protocol
192.0.2.12       HS105(US)    lamp                     off   no        legacy
192.0.2.34       HS110(US)    workbench                on    187 W     legacy
```

Look at the miner's cord before you choose. In the setup that produced
this tool, the plug believed to be on the miner was a different one, on a
different appliance, and the meter reading settled it: a Box hashing at a
manual clock draws a steady 180 W and more; a plug reading 0 W or showing
its relay off is not powering it.

Record the plug:

```
gbox power init --plug 192.0.2.34
HS110(US) 'workbench' (hw 1.0, fw 1.2.6 ...): relay on, 188 W
Is this the plug the miner is powered from? [y/N] y
wrote ~/.gbox/config.json
power block stays in dry run: the watchdog logs 'would cycle' and does nothing until you set "cycle": true
restart `gbox serve` to pick it up; `gbox power status` shows the plug any time
```

`init` stores the plug's device id along with its address. The watchdog
checks the id before every cycle and refuses any other device, so a
changed DHCP lease can never point it at the wrong plug. Restart the
service; its start line in the event log names the plug and the mode.

## What you get right away

- The service line on the dashboard reads, for example, `plug HS110(US)
  on, 188 W, dry run, 0 cycles in 24 h`.
- `log.csv` gains a `watts` column (empty for a plug without a meter, or
  while the plug does not answer). The service migrates an older log on
  start and keeps a `.bak` copy, as always.
- `gbox power status` prints the relay, the watts, the mode, and the
  count of cycles in the last 24 h from the running service.
- A plug that stops answering is one event line on the way out and one on
  the way back. It never fails a miner sample.

## When the watchdog cycles

Only on the frozen-controller signature, all five at once:

1. The miner is **unreachable** (every poll failing for
   `unreachable_minutes`). A miner that answers but has stopped
   producing shares gets soft restarts, as before, never a power cycle.
2. **Two soft restarts in this episode failed** to get through (the
   request timed out or was refused). A restart the controller accepted
   means it is alive and gets its settle time.
3. The episode is at least **`after_minutes`** old (default 5). With the
   default watchdog timings (`unreachable_minutes` 2, `min_gap_minutes`
   5) the second failed restart lands at 7 minutes, so this is where the
   ladder reaches the plug. Once two restarts have failed the age is
   checked on every dark sample, so a longer `after_minutes` moves the
   plug at that age, not a whole gap later at the next restart's turn.
   Until 2026-09-12 the defaults were 15 and 10, which cost about 17
   minutes of hashing per freeze; until 2026-09-13 (0.6.2) the second
   restart also waited for a full `stall_minutes` window of fresh samples
   after the gap, so it landed at about 12 minutes rather than 7 (the
   09-12 18:16 freeze: attempts at 18:17:59 and 18:27:30). The
   dashboard's Service section shows the values in force and the file
   they live in.
4. Fewer than **`max_cycles_per_day`** cycles in the rolling day (default
   3). Each cycle costs two slots of the watchdog's own
   `max_restarts_per_day` (every attempt takes a slot, failed or not), so
   that cap must be at least twice this one; the config refuses anything
   less. A unit that freezes often can run 8 cycles a day against 20
   restarts. Both caps are read back from the event log when the service
   starts, so restarting the service does not hand the watchdog a fresh
   day (on 2026-09-12 it did, and a fourth cycle ran on a hung miner).
5. The plug **answers, is the recorded device, and reports its relay on**.
   A plug that is off was switched off on purpose; the watchdog leaves it
   alone and says so once.

In dry run the log then says `power: would cycle now (miner unreachable
for 2 min; 34 W before); dry run, set "cycle": true in config.json to
arm`, once per episode. Armed, the relay opens for `off_seconds` (default
15), closes, and the log says `power: cycled #1 in 24 h: off 15 s, on
(...)`. Nothing is judged for `settle_minutes` (default 20) while the
miner boots. If it is still dark after that, the whole ladder runs again
before a second cycle, up to the daily cap.

One exception, since 0.7.2: `boot_check_minutes` (default 2) after a cycle
the watchdog reads the meter once. Under `boot_watts` (default 20) the
controller never came up at all: on 2026-09-15 06:40 a cycle left the unit
at 12 W, below even a hung controller's 34 W, and the ladder waited out its
settle gap, 25 minutes, before the cycle that worked. Now it cycles again at
once, once, within the daily cap; a second dark result is logged and left to
the ladder. A good sample before the check clears it. Set
`boot_check_minutes` to 0 to switch the check off.

The meter reading in the line is evidence, not a gate. About 34 W says
the hashboard is idle and the controller hung. A reading near normal
draw with the miner off the network is the rarer case seen on
2026-09-06; the cycle happens either way, and the number is there to tell
the two apart afterwards.

### The deadline (0.13.0)

The fast rung above is held back by the meter (see the config block below):
a reading at working power means the path is down, not the miner. Three times
that reading was wrong. On 10-06, 10-11 and 10-18 the SC-BOX's controller froze
with its hashboard still at full power, answered nothing, and sat for 19 h 38
min, 477 min and 115 min while the watchdog waited for a path that was never
down.

So since 0.13.0 the meter delays a cycle and no longer stops one. A controller
that has not answered at all for `watchdog.dead_minutes` (default 30, 0 off)
is cycled whatever the meter reads. If it is still silent, it is cycled again
`dead_minutes` later, and from then on once every `power.dead_retry_hours`
(default 4). The silence is counted from the last sample it answered, and the
next deadline from the last cycle of either kind. The event log says
`watchdog: silent 12 min and <why>; power cycle at 03:16 unless it answers`
once per episode, then `power: deadline cycle #1 in 24 h: off 120 s, on
(silent 30 min; deadline; 147 W before)`.

Deadline cycles have their own count. They do not use up
`max_cycles_per_day`, and they still run when the fast rung has reached that
cap: the back-off is their loop guard. They never happen:

- for a box drawing under `unpowered_watts`, which the 0.12.1 rule handles;
- while the miner's log shows the pool down, while a hold runs, or during the
  settle gap after a cycle;
- for a controller that answers with an HTTP error or a refused login. That
  controller is alive, and a cut would not help; a wrong password would be cut
  every 30 minutes. A port 4028 timeout is not an answer;
- across a gap in the samples longer than 5 minutes (the computer slept or the
  service paused): the clock starts again from fresh samples;
- when the plug does not answer, is not the recorded device, is off, or this
  computer's own LAN link is down. Each of those says so once and is tried
  again on the next poll.

**A port 4028 timeout and the web.** A sample reads port 4028 first and stops
there when it fails, so until 0.13.0 a `devs 4028` error row said nothing about
the web backend. A frozen controller and a hashing miner with only port 4028
dead logged the same row. Since 0.13.0 the poller makes one web read after a
failed 4028 read and appends its result to the `http` column: `; web ok`, `;
web HTTP 500`, `; web no token`, or the error. It is a record only; the
watchdog does not read it. If a deadline cycle ever cuts a miner whose web
answered `ok` throughout, that row is the evidence that 4028 alone had died.

## Arming it

Watch the dry run through at least one real freeze, or run one deliberate
cycle with the miner in a known state:

```
gbox power cycle
about to cut power to HS110(US) 'workbench' for 15 s. It reads 188 W now (that looks like a miner hashing, not a hung one).
type CYCLE to confirm: CYCLE
cycled: off 15 s, then on. The SC-BOX is back hashing in about a minute (60 to 66 s measured); allow two or three on other units.
event line written to the service log
```

The manual power plan survives a power cycle; the miner comes back at the
clock it was on. Then set `"cycle": true` in the `power` block of
`config.json` and restart the service. `gbox power status` says `ARMED`.

## The config block

```json
"power": {
  "driver": "kasa",
  "host": "192.0.2.34",
  "device_id": "(recorded by gbox power init)",
  "cycle": false,
  "after_minutes": 5,
  "off_seconds": 15,
  "settle_minutes": 20,
  "max_cycles_per_day": 3,
  "idle_watts": 100,
  "unpowered_watts": 10,
  "unpowered_cycle_minutes": 10,
  "boot_watts": 20,
  "boot_check_minutes": 2,
  "dead_retry_hours": 4
}
```

Since 0.10.0 the rung reads the meter just before it acts, and the meter can
only stop a cycle on the fast rung; since 0.13.0 the deadline above cycles a
silent controller whatever the meter reads. At or over `idle_watts` the miner is working and the path
to it is down; under `unpowered_watts` with the relay on, nothing behind the
plug is drawing power (a cord out downstream, the miner's own switch off).
Neither is cycled, and each says so once per episode in the event log (since
0.12.1 the no-load case gets one cycle later; see below).
Between the two is the hung controller (about 34 W), cycled as before. A plug
without a meter behaves as before. `unpowered_watts` must be 0 (off) up to
`boot_watts`, so "no load" always sits inside the boot check's "never powered
up" and the two readings cannot disagree. The boot check's own repeat cycle
is unchanged for every other cycle.

Since 0.12.1 "no load" is not cycled *at first*. On 2026-10-26 the SC-BOX
dropped from 147 W to 1 W with no hang before it: red light, no answer on
the network, and 66 minutes down until a cycle by hand brought it straight
back. That is a power supply that tripped, and it reads exactly like a cord
pulled out downstream. So once the outage is `unpowered_cycle_minutes` old
(default 10; 0 keeps the old "never"), the rung cycles once, and not again
until the miner has answered: the boot check does not add its repeat, and the
ladder says "not cycling again: check the miner's power by hand" from then
on. The mark is cleared only by a sample the miner answers, never by the
ladder's own episode reset, so a one-poll plug or LAN miss mid-outage cannot
buy a second cut. A miner that really is unplugged costs one harmless cut.
The mark is not kept across a service restart, so a restart in the middle of
such an outage allows one more, within the daily cap.

`gbox serve --no-power` ignores the block for one run. Removing the block
removes the feature; nothing else changes.

## Planned outages: holds, off and on, the schedule

Everything above is recovery: the watchdog acting on its own. Since 0.5.0
the same relay is also yours to use on purpose, and the watchdog knows the
difference. Design and reasoning: `power-hold-proposal.md`.

**A hold** tells the service the miner will be unreachable on purpose. While
it runs, the watchdog judges nothing; the poller keeps logging, so the
outage is data rather than a mystery. A hold ends when the miner answers
two hashing samples in a row (about a minute), when it expires, or when you release
it, whichever comes first. So a swap that runs long keeps the hold,
and a swap that finishes early lifts it by itself. Holds count against no
cap. They need no plug: press Hold before you pull the cord.

```
gbox hold                      # one hour
gbox hold 20 --reason "cable"  # 1 to 1440 minutes
gbox hold --no-expiry          # until the miner is back or you release it
gbox hold release
```

**Off, On, Cycle** move the plug and start a hold: Off with no expiry (the
miner stays off, unjudged, until On), On and Cycle for the settle gap so
the boot is not judged. The watchdog never cycles an open relay, a rule it
already had, which is what makes an indefinite Off safe. On the page (served
by `gbox serve`; a browser cannot speak the plug's protocol from a file)
these are in the Controls section. Off and Cycle ask for the miner
password, and the service proves it by logging in to the miner before the
relay moves; if the miner is frozen the page refuses, because that is the
watchdog's job or `gbox power cycle`'s. On asks for nothing: turning a
miner on is what the watchdog already does unasked. From a terminal,
`gbox power off` (typed word `OFF`) and `gbox power on` move the plug
directly, so they work with the service down; with it up they set the hold
and write the line.

**The schedule** switches off and on at set local times:

```json
"power": {
  ...,
  "schedule": {"off": "23:00", "on": "06:00", "days": ["mon", "tue", "wed", "thu", "fri"]}
}
```

`days` is optional (every day). It acts only at its edges: a service that
starts inside an off window leaves the miner as it found it, and your On
inside the window holds until the next off time. Restart the service after
editing; the Service section prints the schedule in force.

**What survives a power cycle.** The board reset counter, the hardware
error counters and the share counters in the firmware start over at boot.
The service's own record does not: it logs them every poll, the trials
table sums increments per segment, and the tiles sum increments over the
last hour. Only the miner's own "since boot" figures start again, and the
page names the boot time next to them.

## Event lines, in one place

| Line | Meaning |
|---|---|
| `hold: started by you until 2026-09-13 07:00:00 (PSU swap)` | a hold with an expiry; `by the schedule` when the schedule set it |
| `hold: started by you, no expiry (switched off)` | a hold that ends only on the miner's return or a release |
| `hold: released, miner hashing again after 12 min` | two hashing samples in a row ended it (until 0.5.1, two answers did) |
| `hold: expired after 60 min with the miner still unreachable; watchdog resumed` | the outage outlived the hold |
| `hold: released by you` | Release on the page or `gbox hold release` |
| `service: hold picked up from the event log (...)` | the service restarted mid-hold and kept it |
| `power: switched off by you (page; 197 W before)` / `switched on by you (page)` | the page's buttons; `(gbox power off)` from the CLI; `by the schedule` |
| `power: cycled by you (page): off 15 s, on (197 W before)` | the page's Cycle button |
| `power: switch off failed: ...` / `power: schedule could not switch off: ...` | the plug did not take the command |
| `service: power plug HS110(US) '...', meter yes, dry run: ...` | the plug answered at start |
| `power: plug unreachable (...)` / `power: plug back` | the plug stopped and resumed answering the poller |
| `power: would cycle now (...)` | dry run: every condition held |
| `power: cycled #N in 24 h: off 15 s, on (...)` | armed: the relay was cycled |
| `power: cycle failed: ...` | the off or the on command got no answer; the on was still attempted |
| `power: the plug is not the configured device (id differs); not cycling` | the address now belongs to another plug |
| `power: plug is off (someone switched it off); not cycling` | left alone on purpose |
| `power: plug did not answer (...); not cycling` | the network, not the miner, may be the problem |
| `power: would cycle (...), but N cycles in 24 h is the cap; not cycling` | the daily cap |
| `watchdog: silent 12 min and <why>; power cycle at 03:16 unless it answers` | 0.13.0: the deadline is running for a controller the meter says is working |
| `power: deadline cycle #N in 24 h: off 120 s, on (silent 30 min; deadline; ...)` | 0.13.0: the deadline cycle; its own count, outside the daily cap |
| `power: deadline reached but ...; trying again each poll` | the plug, or this computer's LAN link, did not allow it |
| `power: the miner answers but fails every sample (...); a power cycle would not help, not cycling` | 0.13.0: an HTTP error or refused login is an answer |
| `service: watchdog picked up N deadline cycles from the last 24 h of the event log` | the service restarted; the deadline schedule carried on |
| `watchdog: hashing at 210 GH/s, under 30% of the rated 900 GH/s, for 10 min; logged only, nothing sent` / `watchdog: hashrate back above 30% of rated after 62 min (...)` | 0.13.0: a slow miner, recorded, never acted on |
| `service: watchdog picked up N restarts and M cycles from the last 24 h of the event log; the daily caps carry on` | the service restarted; the caps did not reset |
| `dashboard: power: cycled by hand (gbox power cycle; ...)` | you ran the command |

The cycle line read `cycled #N today` until 2026-09-18. The counter was always a
rolling 24-hour window rather than a calendar day, and on 2026-09-17 the log said
`#3` while the service reported one cycle, because the earlier two had aged out
of the window. Only the wording changed; the cap it enforces is the same. Logs
written before the change keep the old wording, and both the service and the
dashboard still read it.

## What the page does and does not do

Until 0.5.0 there was no power button on the dashboard, because the plug
has no password of its own for the page to prove. Now there is one, and
the proof is the miner's password: the service logs in to the miner with
what you typed before it opens the relay. The service still has no endpoint
that changes a miner setting, and never gains a clock-changing one. What
`--bind` exposes: anyone on the LAN can read the miner, write log lines,
switch the miner on, and ask the watchdog to hold. They cannot switch it
off, cycle it, or change its clock without the password.

## A note on Kasa plugs and privacy

A Kasa plug on the original protocol answers anyone on the LAN, without
authentication, and one of its replies includes the name of the TP-Link
account that owns the plug. This tool never logs that reply. See
`security-notes.md`.

For how a Kasa plug takes commands from the LAN, the cloud and its own
schedules, and a plug that switched a miner off three mornings running, see
[Kasa smart plugs: how they take commands](kasa-plugs.md).
