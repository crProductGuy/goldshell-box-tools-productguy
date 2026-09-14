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
  on, 188 W, dry run, 0 cycles today`.
- `log.csv` gains a `watts` column (empty for a plug without a meter, or
  while the plug does not answer). The service migrates an older log on
  start and keeps a `.bak` copy, as always.
- `gbox power status` prints the relay, the watts, the mode, and the
  count of cycles today from the running service.
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
15), closes, and the log says `power: cycled #1 today: off 15 s, on
(...)`. Nothing is judged for `settle_minutes` (default 20) while the
miner boots. If it is still dark after that, the whole ladder runs again
before a second cycle, up to the daily cap.

The meter reading in the line is evidence, not a gate. About 34 W says
the hashboard is idle and the controller hung. A reading near normal
draw with the miner off the network is the rarer case seen on
2026-09-06; the cycle happens either way, and the number is there to tell
the two apart afterwards.

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
  "idle_watts": 100
}
```

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
| `power: cycled #N today: off 15 s, on (...)` | armed: the relay was cycled |
| `power: cycle failed: ...` | the off or the on command got no answer; the on was still attempted |
| `power: the plug is not the configured device (id differs); not cycling` | the address now belongs to another plug |
| `power: plug is off (someone switched it off); not cycling` | left alone on purpose |
| `power: plug did not answer (...); not cycling` | the network, not the miner, may be the problem |
| `power: would cycle (...), but N cycles in 24 h is the cap; not cycling` | the daily cap |
| `service: watchdog picked up N restarts and M cycles from the last 24 h of the event log; the daily caps carry on` | the service restarted; the caps did not reset |
| `dashboard: power: cycled by hand (gbox power cycle; ...)` | you ran the command |

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
