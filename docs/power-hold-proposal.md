# Proposal: holds, planned power off and on, and a schedule (0.5.0)

Designed with the owner on 2026-09-12 evening and approved in chat the same
night ("Looks good, build it!"). This file is the design of record for the
feature; `power-cycle.md` is the user-facing guide once it ships.

## The problem

The power rung recovers a frozen controller. Nothing covers a planned
outage. Today an owner who unplugs the miner to swap a power supply, or who
wants it off for the night, gets one of two bad outcomes:

- through the wall switch: the log shows a hole and nothing else, and the
  watchdog reads the outage as a freeze, sends soft restarts to nothing,
  and cycles the plug about twelve minutes in
- through `gbox power cycle`: logged, but a terminal, the checkout, and a
  typed word for what should be one click; and only a cycle, never "off
  until I say"

The reset counter itself is not lost by a cycle: the service logs it every
poll, the trials table sums per-segment increments, and the hour tile sums
row-to-row increments. Only the firmware's own "since boot" figure drops
to zero, which is a rendering matter and out of scope here.

## The design in one paragraph

A **hold** is a state the watchdog owns: the miner is expected to be
unreachable, so nothing is judged, while the poller keeps logging so the
outage is data. A hold ends when the miner answers two consecutive
samples, when it expires, or when it is released, whichever comes first.
Three things start a hold: **Hold** alone (plug untouched, works with no
plug at all), **Off / On / Cycle** through the plug (Off is a hold with no
expiry; On and Cycle a hold of the settle gap), and a **schedule** that
presses Off and On at set times. All three are logged as the owner's own
acts and count against no recovery cap.

## 1. The hold

State on the `Watchdog`: `hold` is `None` or a record with `since`,
`until` (`None` means no expiry), `reason`, `source` (`page`, `cli`,
`schedule`), and `ok_streak`.

Transitions, each one event line:

| Transition | Line |
|---|---|
| start, with expiry | `hold: started by you until 2026-09-13 07:00:00 (PSU swap)` |
| start, no expiry (plug off) | `hold: started by you, no expiry (switched off)` |
| start while one runs | replaces it; same line |
| miner answers twice in a row | `hold: released, miner back after 12 min` |
| expiry with the miner still down | `hold: expired after 60 min with the miner still unreachable; watchdog resumed` |
| explicit release | `hold: released by you` |

Rules:

- While held, `check()` judges nothing. `observe()` still records samples
  and counts consecutive good ones.
- On any release the watchdog's sample window is cleared, so after an
  expiry it needs a fresh full window of samples (five minutes at the
  default poll, the existing rule for gaps) before it acts. Nothing else
  changes: caps, settle gaps and episodes are untouched.
- A hold with no expiry is safe because the rung already refuses to cycle
  an open relay. With the relay on, a no-expiry hold on a running miner
  ends within a minute by the two-sample rule, so it cannot silence the
  watchdog on a healthy unit.
- The two-sample threshold is a guess to be calibrated by use. One would
  release on a flap during boot; five would take two and a half minutes.
- A hold survives a service restart: `seed_from_events` reads the newest
  `hold:` line and restores it unless a later line released it or its
  expiry has passed. The start line carries the absolute time for this.
- Expiry choices on the page: 20 min, 1 h (default), 4 h. Shorter is not
  safer under the two-sample rule: expiry only decides how long an
  abandoned outage runs before the ladder resumes against nothing.

## 2. Endpoints and the password

Three POST routes, JSON bodies, loopback by default like the rest.

| Route | Body | Proof | Answers |
|---|---|---|---|
| `/api/hold` | `minutes` (1 to 1440, or null for no expiry), `reason` (optional, cleaned like an event line) | none | 200 with the hold |
| `/api/hold/release` | none | none | 200 with `hold: null` |
| `/api/power` | `action` off, on or cycle; `password_hex` for off and cycle; `off_seconds` optional for cycle | off and cycle: the miner's password in the encrypted form the page already computes for the miner's own login | 200 with the plug and hold; 403 wrong password; 409 miner unreachable, no plug, wrong device, plug error; 400 bad input |

Why the password rule: the clock button proves the password by logging in
to the miner, and Off is the one action here that stops hashing through
the relay, so it gets the same proof. On cannot be proven that way, the
miner is down, and turning a miner on is what the watchdog already does
unasked. Hold stops nothing but the software, and a LAN client can already
forge log lines, so it needs none. When the miner is frozen, Off is
refused with 409 and a message that names the watchdog and the CLI:
planned outages go through the page, recovery through the watchdog, the
override through a terminal.

The service verifies the password by one login through the serialized
session with the offered hex, discarding the token; its own credentials
are untouched. The hex crosses loopback in plain HTTP, which is what the
page already sends to the miner across the LAN.

All three power actions need a configured plug whose device id matches
the recorded one (the CLI's rule). They do not need the armed flag: arming
governs the watchdog's autonomy, and these are a person's acts. Cycle
runs off, wait, on in a background thread and answers at once; health
says `busy: "cycling"` meanwhile.

`/api/health` gains `hold` (the record or null) and, under `power`,
`off_by_you` (a hold with no expiry is running) and `busy`.

## 3. Poller and watchdog while held

The poller changes nothing. Error rows stay error rows; the outage is
data. The interventions table reads the new lines with its existing
parser: `hold:` and the new `power:` lines are the owner's, and the gap
between an off line and a back line reads as planned.

## 4. The page

A Power block in the Controls section, served mode only: the browser
cannot speak the plug's protocol, and the standalone file says so. Off,
On, Cycle; Hold with an expiry select; Release. Off and Cycle open the
existing confirm dialog with the password field, the plug's current
watts, and the same "that looks like a miner hashing" note the CLI
prints. On and Hold confirm without a password. Without a configured plug
the three plug buttons are disabled with a note; Hold and Release stay.

The Service section shows a running hold: until when, why, who, and that
it lifts on the miner's return. Health is polled as today, so the banner
clears by itself.

## 5. The schedule (step two)

A `schedule` object inside the `power` block of `config.json`:

```json
"schedule": {"off": "23:00", "on": "06:00", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}
```

`days` optional (every day). Validated at load: both times `HH:MM`, days
from that list, and a schedule needs a plug. The poller thread already
ticks every poll; a scheduler compares the wall clock with the last tick
and presses Off or On when a set time falls between them. Edges only: a
service starting inside an off window does nothing, and an owner's On
inside an off window holds until the next off time. Lines read `power:
switched off by schedule` and `power: switched on by schedule`. No
password: editing the config file is the owner's act. The Service section
prints the schedule beside the ladder.

## 6. CLI

`gbox hold [MINUTES] [--reason TEXT]` and `gbox hold release` talk to the
running service. `gbox power off` (typed word OFF) and `gbox power on`
move the plug directly, as `power cycle` does, so they work with the
service down; with it up they set the hold through `/api/hold` and post
the event line. `power cycle` gains the hold for the settle gap.

## 7. Tests and done-when

- Watchdog: start, release by two samples, release by expiry, explicit
  release, no-expiry hold, seed from the event log, and that a hold on a
  running miner ends in two samples.
- Server, against the fake miner and fake plug: right password, wrong
  password, miner down (409), on without a password, cycle answering at
  once, hold and release, bad input, health fields.
- Page under Node: the request builders and the banner text.
- Scheduler with a fake clock: fires at an edge, not before, not twice,
  respects days, does nothing at start inside a window.
- CLI: hold and release through a scratch service.
- Done-when: all green; in Chrome on a scratch service every button and
  the banner; then on the live service the owner presses Hold 20 min and
  Release, and Off then On on the real miner, and the log shows the lines
  and a reset tally that does not drop.

## 8. Docs and version

`plan.md`'s "No power button on the page" decision is replaced by this
one with its reason. `power-cycle.md` gains "Planned outages: holds, off
and on, the schedule". README and `security-notes.md` updated. Version
0.5.0 (new endpoints and CLI commands: a minor bump). The other-models
work moves to 0.6.0.

## Out of scope, said so

A graceful shutdown: the firmware has no such endpoint, so a hard cut is
the only off there is. Auto-release for a plug Off. The schedule on the
standalone page. Whether daily hard cuts wear the controller's flash: not
verifiable here; the recovery rung has already cut it many times without
harm, and the log will show a controller that fails to boot.

## Changed in 0.5.1 (2026-09-13)

"Answers two consecutive samples" became "hashes for two consecutive
samples". On the first real Off and On, the controller came back without
its hashboard: HTTP answered, hashrate 0, board sensor reading -150, 9 W
at the wall. Two answers released the hold, and the stall rule happened
to cover the gap five minutes later with a soft restart that brought the
board up. Now the poller passes whether the sample reported a 20 s
hashrate, and only hashing samples count toward release. The line reads
`hold: released, miner hashing again after N min`.
