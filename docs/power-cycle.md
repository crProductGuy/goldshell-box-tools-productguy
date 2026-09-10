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
authentication). That covers HS100, HS103, HS105, HS110, KP115, KP125,
EP10 and similar units that have not taken a recent firmware update.

Energy readback needs a plug with a meter. Kasa models that have one:
**HS110, KP115, KP125, KP125M, EP25, HS300** (the six-outlet strip; not
yet supported here). **The HS100 family has no meter**, on any
hardware version (HS100, HS103, and HS105 alike); they can still switch
the miner, and the service shows only the relay state.

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
3. The episode is at least **`after_minutes`** old (default 15). With the
   default watchdog timings the second failed restart lands at about 17
   minutes, so this is where the ladder reaches the plug.
4. Fewer than **`max_cycles_per_day`** cycles in the rolling day (default
   3).
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
cycled: off 15 s, then on. The miner takes 2-3 minutes to boot and start hashing.
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
  "after_minutes": 15,
  "off_seconds": 15,
  "settle_minutes": 20,
  "max_cycles_per_day": 3,
  "idle_watts": 100
}
```

`gbox serve --no-power` ignores the block for one run. Removing the block
removes the feature; nothing else changes.

## Event lines, in one place

| Line | Meaning |
|---|---|
| `service: power plug HS110(US) '...', meter yes, dry run: ...` | the plug answered at start |
| `power: plug unreachable (...)` / `power: plug back` | the plug stopped and resumed answering the poller |
| `power: would cycle now (...)` | dry run: every condition held |
| `power: cycled #N today: off 15 s, on (...)` | armed: the relay was cycled |
| `power: cycle failed: ...` | the off or the on command got no answer; the on was still attempted |
| `power: the plug is not the configured device (id differs); not cycling` | the address now belongs to another plug |
| `power: plug is off (someone switched it off); not cycling` | left alone on purpose |
| `power: plug did not answer (...); not cycling` | the network, not the miner, may be the problem |
| `power: would cycle (...), but N cycles in 24 h is the cap; not cycling` | the daily cap |
| `dashboard: power: cycled by hand (gbox power cycle; ...)` | you ran the command |

## What the page never does

There is no power button on the dashboard, on purpose. The service has no
endpoint that changes the miner's state, so exposing it with `--bind` is
exactly as safe as before. A deliberate cycle is a terminal command with a
typed word.

## A note on Kasa plugs and privacy

A Kasa plug on the original protocol answers anyone on the LAN, without
authentication, and one of its replies includes the name of the TP-Link
account that owns the plug. This tool never logs that reply. See
`security-notes.md`.
