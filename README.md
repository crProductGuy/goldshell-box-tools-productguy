# goldshell-box-tools-productguy

Status: pre-release, under construction. See `STATUS.md` and `docs/plan.md`.

A small, dependency-free toolkit for Goldshell Box-series miners (SC-BOX,
KD-BOX, HS-BOX, LT-BOX and relatives running the "cloud-box" MCB_V5 firmware):

- a status dashboard that shows what the stock web UI hides: the hottest
  chip's temperature (the firmware's own chip field is the board average,
  and the hottest chip sits 10 C or more above it), per-chip health, the
  board reset counter, fan duty, hashrate,
  fan and temperature history, power at the wall from a metering smart
  plug, right-hand axes in percent of the model's rated figures, and a
  table of every intervention the software (or you) made, with how long
  the miner took to come back
- protected buttons for the settings whose Save button on the stock UI is a
  trap: manual clock in 25 MHz steps, fan target, firmware preset, and a
  soft restart. Each shows what changes and the exact request before
  sending it; clock, preset, and restart ask for the miner password again
- a logger and a watchdog that soft-restarts the miner when it stops hashing,
  and, with a Kasa smart plug on the miner's cord, power-cycles a frozen
  controller that a soft restart cannot reach (dry run until you arm it)
- a clock-trials table, on the dashboard and as `gbox trials`, that compares
  every clock and fan target the miner has run: worst-chip bad share, board
  resets, HW error rate, shares per hour, hashrate, watts and GH/s per watt
  (with a metering plug), temperature, fan speed.
  `gbox trials run 550 575 600 --hours 4` steps through a list unattended
  and backs off to a safe clock at the first board reset
- charts over days, not hours: hashrate, fans, temperatures and power over
  the last 24 hours from the service log, and an errors chart over three
  days showing the bad share of all chips and of the worst chip per half
  hour, the clock, and board resets, so a bad night is visible the next
  morning. The same buckets as text: `gbox errors`
- a command line: `gbox status | chips | plan | fantarget | restart | trials | errors | power | hold | serve`

Born from a diagnosis of an SC-BOX running at 30 percent: one marginal chip was
resetting the whole board every nine seconds at the factory clock, and the
stock UI offered no way to see it or to lower the clock. The story is in
`docs/case-study-scbox.md`.

## Quick start

Python 3.8 or newer, nothing else. Clone, then:

```
python -m gbox discover             # find the miner on your network; no password needed
python -m gbox init                 # miner address, poll interval -> ~/.gbox/config.json
python -m gbox status               # prompts for the miner's web UI password
python -m gbox serve                # dashboard at http://127.0.0.1:8765/
```

Or `pipx install .` to get a `gbox` command on your PATH.

If you do not know the miner's address, `gbox discover` sweeps your own /24
and prints a line per unit it finds: address, the model string the firmware
gave, the name this project knows it by, firmware and hardware. It asks each
address one thing, `GET /mcb/status`, which the firmware answers without a
token, so nothing is logged in to and no password is read or sent. Use
`--subnet 192.168.1.0/24` if the miner is on another segment, and `--timeout`
if your network is slow. The miner already in your `config.json` is listed but
not probed, because a running `gbox serve` may have a request in flight to it
and the firmware wants one caller at a time; `--include-configured` asks it
anyway, and is for when the service is stopped. The command exits 1 when it
finds nothing.

Open the dashboard, log in once with the miner's web UI password. The page
keeps only the session token, in your browser, and hands it to the local
service so the logger and watchdog can run without a password on disk. If the
service should survive a reboot without anyone opening the page, use
`gbox serve --remember`: it stores the password in the firmware's own
encrypted form, which is password-equivalent, so the file is owner-readable
only. `gbox serve --forget` removes it.

The dashboard also works with no service at all: open `gbox/web/index.html`
straight from disk. You get live status; the fan and temperature history and
the event log need `gbox serve`.

Start at logon:

- Windows: `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`
  (no administrator rights needed; `-Uninstall` reverses it)
- Linux with systemd (Ubuntu, Debian, Fedora and most others):
  `scripts/install-linux.sh` writes a user unit, enables it, and turns on
  linger so the service runs with nobody logged in and after a reboot
  (`--uninstall` reverses it; `--dry-run` prints every step and touches
  nothing). No root needed, except that some distributions ask for
  `sudo loginctl enable-linger $USER` once, and the script says so. A
  fresh box needs three commands: clone, `python3 -m gbox init`, then the
  script. Without systemd (Devuan, Alpine), run `python3 -m gbox serve`
  from the clone under your init's supervisor; the script says as much.

The data directory does not grow without limit. At a 30-second poll the log
gains about half a megabyte a day, so `log.csv` is capped: when it reaches
`log.max_mb` in `config.json` (25 MB, about six weeks), the last
`log.keep_hours` of rows are carried into a fresh file and the whole old one
is kept beside it as `log.csv.1`. Nothing reads `.1`, so the charts, the
errors chart and the trials table show exactly what they showed a moment
before: the rotation is invisible on the page, and the only sign of it is a
line in the event log. What it does cost is history: trial segments older
than the carry are archived rather than shown. `boards.csv` is capped the
same way, `events.log` at 5 MB with its last 4000 lines carried, and
`"max_mb": 0` turns all of it off if you would rather keep everything and
watch the disk yourself. Delete or move a `.1` file whenever you like.

## Power-cycling a hung miner

A frozen controller drops off the network and cannot take the watchdog's
soft restart; only a power cycle clears it. If the miner is plugged into a
TP-Link Kasa smart plug on its original local protocol, the service can do
the cycle itself, and read the plug's energy meter where the model has one
(KP115, KP125 and the end-of-life HS110 on the original protocol; the
HS100/103/105 have none; the full list is in the guide):

```
python -m gbox power discover                 # which plugs answer on the LAN, with their meters
python -m gbox power init --plug 192.0.2.34   # record the miner's plug; stays in dry run
python -m gbox power status                   # relay, watts, dry run or armed, cycles in 24 h
python -m gbox power cycle                    # a deliberate cycle, after typing CYCLE
```

In dry run the event log says "would cycle" and nothing moves. The watchdog
cycles only when the miner is unreachable, two soft restarts have failed,
five minutes have passed, the plug is the recorded device with its relay
on, and the daily cap has room. The dashboard's Service section spells out
the timings and caps in force and names the config file they live in. Arm it with `"cycle": true` in the config
block once you have watched it judge a real freeze. The guide is
`docs/power-cycle.md`; it also says which plugs and PDUs fit larger miners.

### Planned outages: holds, off and on, a schedule

A planned outage should not look like a freeze. Since 0.5.0 the service
knows about **holds**: tell it the miner will be unreachable on purpose and
the watchdog judges nothing until the miner hashes twice in a row, the
hold expires, or you release it. The dashboard's Controls section has a
Power block for all of it when the page is served; the same things from a
terminal:

```
python -m gbox hold 30 --reason "PSU swap"   # before you pull the cord; needs no plug at all
python -m gbox hold release
python -m gbox power off                     # switch the miner off on purpose, after typing OFF; stays off until `on`
python -m gbox power on                      # switch it on; the watchdog waits for the boot
```

On the page, Off and Cycle ask for the miner password and the service
checks it with the miner before the relay moves; On and Hold ask for
nothing. A `schedule` block in the power config switches the miner off and
on at set times. The reset counter and the rest of the history survive all
of this: the service logs every poll, and the tiles and the trials table
sum increments across boots. Details in `docs/power-cycle.md`.

## The debug page you were never shown

The stock web UI has a hidden page, `http://<miner>/#/debug`, that shows
per-chip good and bad nonce counts, the fan controller's log, and the
miner's own logs. It is where a weak chip is visible while the Miner page
still looks healthy. `docs/stock-ui-debug-page.md` says how to open it,
what each part is, and why not to leave it auto-refreshing.

## Finding the right clock

Lower the clock until the weak chip stops producing bad nonces and board
resets, then climb back up 25 MHz at a time while the Clock trials table on
the dashboard says the chip is still clean. Hold each clock for hours, not
minutes. `docs/clock-tuning.md` has the method, by hand and with
`gbox trials run`, and explains why accepted shares per hour is a worse
throughput number than it looks.

## Which miners

Support is claimed only where someone has run gbox on the unit. Everything
else in the table is what the code was built from, stated as that.

| Model | Standing | Built from |
|---|---|---|
| SC-BOX | supported | developed and run daily on one unit |
| SC5 Pro II | built, not yet run on a unit | a friend's capture (four boards, four fans, firmware 2.2.0): every board read, logged to `boards.csv` and shown, plus the firmware's own DC power figure. Claimed when the owner runs it |
| SC5 Pro | assumed | the spec sheet, and the SC5 Pro II's behavior; the model string has not been read from a unit |
| SC-BOX II | assumed | retailer listings; the model string has not been read from a unit |
| SC Lite | not yet | another developer's notes and synthetic fixtures; the per-board read for this model is not built |

Any other Goldshell unit gets the SC-BOX path with the optional features
off, and the page says so under its title.

Own one of the unclaimed models, or a different one?
`docs/capture-request.md` says which read-only requests to capture (two
socket reads, seven `curl` reads), what to redact, and where to send them,
so gbox can learn your unit without one on the bench.

## Rules the tools follow, and you should too

- One request in flight to the miner at a time, and never more than one poll
  cycle per 10 seconds. The firmware's token check has a race and its web
  backend crashes under bursts. Details in `docs/firmware-api.md`.
- Never press Save in the settings block of the stock UI's Miner page while
  the miner runs a manual clock. That block holds the power plan dropdown
  and the fan target slider, and its Save handler always writes `manual:
  false`, so nudging the fan slider there silently returns the clock to the
  factory preset. Use the dashboard's Controls instead. The rest of the
  stock UI writes to its own endpoints and is safe for the plan:

  | Stock UI | Safe with a manual clock? |
  |---|---|
  | Miner page, settings block Save (power plan, fan target) | **No** |
  | Miner page, pools add/delete | Yes |
  | Miner page, algorithm, LED/RGB | Yes |
  | System page: IP, WiFi, restart | Yes (the manual plan survives a restart) |
  | System page: factory reset | Never |
- The service listens on 127.0.0.1 only unless you pass `--bind`. Anyone who
  can open the page can read the miner, write lines into the event log and,
  with the miner password, press the buttons. The page sends every settings
  change to the miner directly; the service only records what happened.
  The plug is the one thing the service moves for the page: Off and Cycle
  carry the miner password, which the service checks with the miner before
  the relay opens; On and Hold need none, because turning a miner on is
  what the watchdog already does unasked, and a hold only stands the
  software down. So with `--bind`, someone on your LAN could switch the
  miner on or ask the watchdog to wait; they could not switch it off or
  change its clock without the password.

## How this was built

An owner with one tired miner and an AI coding agent, over ten days in
September 2026. `docs/how-this-project-evolved.md` is the story, from the
first "why is it hashing at 30 percent" through the clock trial that
reproduced two days of observation in 34 minutes, to the smart plug that
brought the miner back seven times in one night.
`goldshell-box-tools-productguy-EVOLUTION.md` is the session-by-session record behind
it: the prompts, the questions, the decisions and why.

## Development

```
python -m unittest                  # fixtures + a fake miner, no hardware needed
python -m tests.fake_miner          # a fake miner on :8999, password "password"
python -m gbox --host 127.0.0.1:8999 --data /tmp/gbox-dev serve --port 8770
```

License: MIT.
