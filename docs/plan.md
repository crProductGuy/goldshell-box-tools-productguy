# Plan: goldshell-box-tools-productguy

Decided with Mark on 2026-09-05. This file is the design of record for the
public release. `STATUS.md` at the repo root tracks progress against it.

## Goal

A public, MIT-licensed toolkit at github.com/crProductGuy/goldshell-box-tools-productguy
that lets a somewhat-technical owner of a Goldshell Box-series miner see what
the stock UI hides, change the settings the stock UI cannot reach, and keep
the miner hashing unattended. Install must be easy on Ubuntu-class Linux and
on Windows: standard-library Python only, one process, one command.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Repo name | `goldshell-box-tools-productguy` (renamed 2026-09-09 from `goldshell-box-tools`); package name `goldshell-box-tools`, command `gbox` | "goldshell box" stays in the name for findability, and other Box models share the firmware; the suffix ties the repo to Mark's nym among many generic repos. A `pg-` prefix was rejected because `pg` reads as PostgreSQL. GitHub redirects the old name. The package and command did not change. |
| License | MIT | Bitcoin Core's license; shortest; no obligations on forks. GPLv3 was the alternative, rejected for a gift. Apache-2.0 adds a patent grant nothing here needs. |
| Secrets | none on disk by default | user preference. The dashboard pop-up is the only place a password is typed. Opt-in `--remember` stores it in `config.json` with owner-only permissions for unattended reboots, and the README says what that trades away. |
| Standalone page | yes | `gbox/web/index.html` must work opened as a file with no service: live status and the read-only view. The service adds the fan/temperature history, the event log and reboot survival. |
| Dependencies | Python standard library only | install = clone (or `pipx install .`) and run; no compiler, no pip resolution failures for near-normies |
| Process model | one process, `gbox serve` | logger thread + watchdog thread + local HTTP server; one thing to start and stop |
| Bind address | 127.0.0.1 by default | anyone who can open the page can press the buttons; LAN exposure is an explicit flag with a warning |
| Source of truth for the current running tools (until 2026-09-05) | `Projects/scbox-tools/` (not a repo) | kept running untouched while the package was built; ported from, not moved. Retired 2026-09-05 when `gbox serve` replaced its three scheduled tasks. |
| Data directory | `~/.gbox` (`GBOX_DATA` or `--data` override), not `data/` in the repo | a `pipx install` has no repo directory to put `data/` in. Decided during step 1; supersedes item 1 under "What must change". |
| Log format | `log.csv` columns are only ever appended, never renamed or reordered; the service migrates an older log in place on start and keeps a `.bak` | the dashboard's charts and the clock-trials table read columns by header name, and a user's history must survive an upgrade. First exercised 2026-09-08 (17 to 21 columns). |
| Unattended clock stepping | `gbox trials run` is a CLI process that talks to the miner itself; the service only serves its progress file read-only | the service never gains a clock-changing endpoint, so `--bind` exposure stays exactly as safe as it was. Cost: a trial dies with its terminal. |
| Versioning | `0.x`; bump the minor version when the log format, the CLI surface, or the HTTP API changes; tag every release `vX.Y.Z` | users on an older log need to know which version changed what; the `~` marker in the trials table refers to "before 0.2.0". |
| Power rung (2026-09-10) | an optional `power` block in `config.json`; the watchdog cycles a smart plug only on the frozen-controller signature (unreachable, two failed soft restarts, `after_minutes`, under the daily cap, the recorded device with its relay on); **dry run by default** | three controller freezes that only a power cycle clears (`firmware-api.md`). Dry run first because a tool that can cut a miner's power must prove its judgment in the log before the relay moves. The design and the research behind it: `power-cycle-proposal.md`. |
| ~~No power button on the page~~ Holds and planned power (2026-09-12, 0.5.0) | a **hold** on the watchdog (nothing judged until the miner hashes twice in a row, the hold expires, or it is released); Off, On and Cycle on the page through `/api/power`, each under a hold; `gbox hold`, `gbox power off`, `gbox power on`; a `schedule` block. Off and Cycle carry the miner password and the service checks it by a login; On and Hold need none. Design: `power-hold-proposal.md` | a planned outage looked exactly like a freeze (restarts to nothing, then a plug cycle twelve minutes in), and a cycle by the wall switch was invisible to the log. The old reason against a button, that the plug has no password to prove, is answered by proving the miner's: someone who can prove it can already set 725 MHz. Mark chose auto-release on the miner's return over an explicit release ("less user planning and cognitive load") |
| Plug identity | `gbox power init` records the plug's device id; every cycle checks it and refuses any other device | the plug believed to be on the miner turned out to be a different one on a different appliance (2026-09-09); a DHCP change must never point the watchdog at the wrong relay |
| Watts in the log | `watts` is the appended 22nd column, empty without a meter | evidence in the event line and the log first; a gate on watts is one line to add if the evidence says so. Since 0.4.0 also the input for the watts and GH/s-per-watt columns in the trials table and the watts chart. |
| Watts per run (0.4.0) | a segment's watts is the mean over the rows that have a reading; `watts_n` says how many; GH/s per W is the segment's mean hashrate over that mean, always in GH/s; a rollup weights watts by `watts_n`; rows without any reading show "?" | the meter reads the same wall load whether or not every poll caught it, so partial coverage is not flagged; runs logged before the plug (all the 2026-09-05 to 09-10 trials) keep their Kill A Watt figures in `clock-tuning.md` rather than a manual table, by Mark's choice |
| Rated figures (0.4.0) | `gbox/models.py` (mirrored in `app.js`, a test keeps them equal) keyed by the `/mcb/status` model string: rated hashrate and watts, fan count and max RPM, boards, and the source of each number; the charts draw a right-hand "% of rated" axis from it; fan percent is RPM over max RPM, not the firmware's duty cycle | only the SC-BOX string has been read from a unit; the SC-BOX II and SC Lite rows come from spec pages and the other developer's notes and say so; an unknown model gets no percent axis and a caption saying why. Reading the duty cycle every poll would add a few-hundred-KB `/dbg/fanctrllog` fetch to each cycle, refused on the one-request-per-10-s rule |
| Interventions table (0.4.0) | the page filters the event log to `watchdog:` restarts, `power:` cycles, `dashboard:` actions and `service: started`, newest first, and joins each restart or cycle to the log rows for "miner back after N s"; `/api/events` tails 2000 lines | Mark asked for a rolling record of what the software did to the miner, including his own actions; the join is pure JS over data the page already has, so the service gains no endpoint |
| 0.4.0 API additions | `/api/health` gains `model`, `rated` and `power.alias`; `/api/trials` rows gain `watts`, `watts_n`, `gh_per_w` | the minor bump under the versioning rule; nothing renamed or removed |
| 0.5.0 API additions | `POST /api/hold`, `POST /api/hold/release`, `POST /api/power`; `/api/health` gains `hold`, `power.off_by_you`, `power.busy`, and `ladder` gains `idle_watts`, `off_seconds`, `schedule`; new CLI `hold`, `power off`, `power on` | the minor bump; nothing renamed or removed. The other-models work moves to 0.6.0 |
| The operator's view over days (2026-09-13, 0.6.0) | every chip's counts logged every poll in one appended `chips` column (`board.chip:good/bad`); `GET /api/series?hours&bucket` serves the log bucketed (means, increment sums with the counter-reset rule, the worst chip, events), cached by the log's mtime; the served page draws every chart over 24 h from it and adds a three-day errors chart (bad share, worst chip, clock as a step, resets as bars); `gbox errors`; `/api/log.csv?tail=N`. Design: `charts-proposal.md` | Mark: "for the human operator to see when things really went bad on a graph, so she/he can do something." Bad share rather than bad count because a faster clock attempts more nonces; resets on the same chart because the 2026-09-13 16:02 event was 46 resets and 9 bad nonces. No new miner requests: the chips data is in the icinfo request already made. The other-models work moves to 0.7.0, with log rotation |
| Ladder timing (2026-09-13, 0.6.2) | after the settle gap the unreachable rule reads its own `unreachable_minutes` window, which may reach back into the gap, so a controller still dark at the gap's end gets the next rung on the next sample; the stall rule still needs a full window after the gap; once two restarts have failed, `after_minutes` is checked on every dark sample | the second soft restart landed about 10 min after the first, not 5 (09-12 18:17:59 and 18:27:30), because every rule waited for a full `stall_minutes` window of fresh samples after the gap before the 2-minute rule got a look; the plug reached a frozen controller at 12 min where the docs promised 7. Mark chose 7 over "2 min of fresh failures after the gap" (about 9): same evidence, two minutes sooner |
| 0.6.0 API additions | `chips` column 23; `GET /api/series`; `/api/log.csv?tail=N`; CLI `errors` | the minor bump (log format, HTTP API and CLI all changed); nothing renamed or removed |
| The model seam (2026-09-13, 0.7.0 gate 1) | `gbox/models.py` rows carry a capability profile (`plan_dialect`, `board_source`, `dbg_expected`, `fan_target`, `temp_target_basis`) and `profile_for` gives an unknown model the SC-BOX path with every optional capability off and `known: False`; `/api/health` carries it and the page says so under the title. The plan string is parsed in its three dialects (`firmware-api.md`, "Power plan dialects") from the string itself, and the clock control rewrites only the MHz token (`with_mhz` / `withMhz`). Synthetic SC Lite fixtures in `tests/fixtures/sclite` | Part C of the 2026-09-12 plan: the seam first, so gates 2 (per-board sampling, `boards.csv`, per-board panels) and 3 (`gbox discover`, log rotation) add to it without touching the BOX path. String-driven parsing rather than table-driven because the table is built from another lab's notes and the string is what the unit actually wrote; rewriting one token rather than formatting from parts because nobody has confirmed what the SC Lite's integer volts or PV mean. No release bump until the gates land; `/api/health` gained a field, so 0.7.0 is the minor bump |
| The hottest chip (2026-09-14, 0.7.0) | every `syslog_interval` seconds (300 default, floor 60, 0 off) the poll cycle makes a fourth serialized request, `/dbg/minersyslog`, and writes three appended columns on that row: `hot_peak`, `hot_level` (the median of the 5-second `MaxTemp` readings since the last read), `chip_avg`; other rows leave them blank. The log text is never stored (it repeats the pool user); a cursor on the miner's own timestamp keeps a read to new lines, and the first read after a start takes the last 5 minutes. Thresholds `temps.hot_serious` 85 and `temps.hot_critical` 90 in `config.json`, served in `/api/health` with `syslog_interval`. The tile becomes "Hottest chip": the sustained level is the number and the flag, the peak and the chip average beside it, the since-boot highs beneath. The temperature panel draws board (blue), chips average (amber) and hottest chip (red, thicker) with a band up to the peak and the serious line, every line named at its end | nothing else reports the hottest chip; `tstemp-0` is the average plus 3 and had under-reported it by 10 to 15 C. The flag is on the level because the peak reads 90+ a few times an hour at normal operation and a flag on it would never go out. 2 MB every 30 s would be 70 KB/s from a web backend that crashes under bursts; every 5 minutes is one request per ten cycles and 60 samples per median. Log format and API changed, so the minor bump: 0.7.0 ships this, and gates 2 and 3 move to 0.8.0 |
| The boot check (2026-09-15, 0.7.2) | `boot_check_minutes` (2) after a cycle the watchdog reads the plug's meter once; under `boot_watts` (20) it cycles again at once, once, within the daily cap, and logs a second dark result for the ladder. A good sample clears the check; 0 minutes switches it off. Served in the ladder block | cycle 1 on 2026-09-15 left the controller at 12 W for 25 minutes (never booted) until the ladder's settle gap ran out; Mark: "I like your idea", built as its own gate because it changes when the plug fires |
| Every board of a multi-board unit (2026-09-15, 0.8.0 gate 2) | one per-board record, produced by two parsers over two transports that carry the same field names: the `[PGAn]` blocks of `/dbg/minerinfo` (HTTP, token) and the JSON of `devs` on TCP 4028 (no token). `board_totals` folds the boards into the dict every caller already uses, with the temperatures taken from the hottest board, so the SC-BOX's numbers and log columns are what they were. The service reads 4028 first and falls back to `/dbg/minerinfo` for the rest of the run with one event line (`board_source`: `auto`, `4028`, `minerinfo`); the page reads `/dbg/minerinfo`, because a browser cannot open a socket. Appended column `watts_dc` (the firmware's voltage times current, where the unit reports both); `boards.csv` beside `log.csv` on units with more than one board; `GET /api/boards`; a Boards table on the page, hidden on one board; `plan_names` per model. An icinfo 401 that persists blanks the chips columns and no longer fails the sample; so does a 404 or 500 from a unit that lacks the endpoint, but only until icinfo has answered once in the run, so on the SC-BOX a failed icinfo read stays a failed sample (a blank row there would put a zero in `nonces_good`; `series.inc` reads that as a counter reset and adds the run's whole count again on the next row, and the trials' first-to-last difference breaks) | a friend's SC5 Pro II capture showed four PGA blocks where `parse_minerinfo` read only the first, so 0.6.2 had shown its owner one board of four. The alternative, planned first, read the HTTP wrapper `/mcb/cgminer?cgminercmd=devs`: rejected when the capture showed it needs a third parser (different key names, temperatures as "77.7 °C" strings) and the `/dbg/` 401s it was meant to route around turned out to be one-offs. 4028 first in the service because it skips the web backend, so neither the token race nor the burst crash applies. A second file for boards because a variable board count does not fit one append-only header. The page's fallback to the HTTP wrapper was dropped (Mark): a parser for a lock no unit has shown. Model value `devs` renamed `http_devs` so it stops reading like the cgminer command. Log format and HTTP API changed, so the minor bump: 0.8.0, with gate 3 |
| Finding a miner, and a data directory that stops growing (2026-09-19, 0.8.0 gate 3) | `gbox discover` sweeps the machine's own /24 (or `--subnet`, up to a /22) with one tokenless `GET /mcb/status` per address and prints a line per unit: address, model string, the name this project knows it by, firmware, hardware. No credential is read, stored or sent, and the configured miner is listed but not probed unless `--include-configured` says so. Rotation: `config.json` gains `log: {max_mb: 25, keep_hours: 72}`; at the cap `log.csv` and `boards.csv` carry the last `keep_hours` (plus a 2 h margin) into a fresh file and the whole old one becomes `.1`, `events.log` carries its last 4000 lines at 5 MB, and `max_mb: 0` turns it all off. One event line per rotation; `/api/health` gains `log: {bytes, max_mb}`; the page's Service line ends with "log 8.4 of 25 MB" | `/mcb/status` is the only tokenless path that carries a model string (port 4028 answers without a token but names no model), so the probe needs no password and cannot lock anyone out. The carry is what makes rotation invisible: a bare rename would have blanked the 24 h charts, the three-day errors chart and the trials table at the moment it fired. The margin is not decoration -- carrying exactly `keep_hours` left the oldest bucket of a 72 h reader half full on real data, and the errors chart asks for exactly 72 h. The trade Mark chose over readers that span two files: trial history older than the carry is archived, not shown. `.1` is an archive nothing reads, so the rows it duplicates are never double-counted |
| LAN discovery reads the OS, never the route (2026-09-20, 0.8.1) | `gbox/netiface.py` is the only place that decides what this machine's network is: it reads the interface table (`Get-NetIPAddress` plus `Get-NetAdapter` on Windows, `ip -4 -o addr` plus sysfs on Linux, `ifconfig -a` on the BSDs) and applies nine hard rules, tested one by one. A tunnel is excluded by what the interface is; the prefix is the interface's own; the routing table is never consulted; 169.254.0.0/16 and disconnected links are never networks; a LAN overlapping a tunnel is refused; two LANs are a question; a wholly unreachable sweep exits 2 rather than reporting no miner | the address-based check from 0.8.0 was the right fix for a VPN with a public range and the wrong test in general, because a corporate or WireGuard VPN hands out RFC1918. The owner's rule: LAN is LAN, no further reach. Reading the interface also ends the /24 assumption, which was correct on the machine it was written on and nowhere else by design |
| The evidence an incident needs, and one rule it paid for (2026-09-20, 0.9.0) | `volts` appended to `log.csv` and `boards.csv` (the firmware's figure, unconverted); `minerlog.csv`, one row per label per log read, written from the `/dbg/minersyslog` read the service already makes, by `api.classify_syslog` (a fixed whitelist of line shapes; labels, counts and digit timestamps only; own cursor; stops at 5 MB); `watchdog.absent_minutes` (2, 0 off): HTTP answering with clock 0 and the board sensor at its no-sensor value for that long is a soft restart, on a model whose profile carries `absent_signature` (the SC-BOX only) | two incidents that day could not be explained from what was kept: the voltage was parsed and dropped, and the miner truncated its own log (3.8 MB to 36 KB, no restart) before it was read. Labels, not masked text, because the log repeats the pool user and a mask is a blacklist that fails open. The rule is the one the 15-day log supported: 12 reset bursts all healed alone in under 2.5 min, so the stall window stays at 5; 6 board-absent episodes never healed alone, so that signature alone gets 2. Never a power cut, and no shorter window for a board that is present: 3 of 4 soft restarts sent to one lost the board. Minor bump: the log format changed |
| A cold boot's clock, and a service started during one (2026-09-21, 0.9.1) | The miner log is read by position, not by timestamp: `api._after_cursor` finds the line the cursor names and reads what follows it, and `parse_chiptemps` keeps only what follows the newest `Init sucessed` or `Started intminer` line. The `auto` board source is decided only once a transport answers. | After an outage on 2026-09-21 the miner's clock read 2007 until it was set, while the log still held the run before. The next read counted that run into `minerlog.csv` a second time, and the first row after the boot carried the old run's 79 C peak. The service had also started while port 4028 was not up yet, fell back to `/dbg/minerinfo` for the whole run, and so logged no `volts`. Patch bump: no format, CLI or API change |
| Plug drivers | Kasa legacy protocol first (both plugs on hand); Shelly Gen2 and Tasmota next; a generic HTTP driver for REST PDUs; KLAP last | standard-library rule, so every protocol is reimplemented; Shelly outnumbers Kasa two to one among local-control users; KLAP needs credentials and cannot be verified without a device |

## What must change from `scbox-tools`

1. Secrets and personal data out: `pw.hex`, CSV/logs, miner-syslog copies (carry the pool wallet), `setting_before.json` (carries the unit's MAC). All runtime state outside the repo, in `~/.gbox` (see Decisions; `data/` was the original idea).
2. No hard-coded miner address, nominal hashrate, poll intervals: config + prompts.
3. Chip count and board count read from the miner (`icinfo.drawdata` is an array of boards). Hashrate auto-scaled from the MH/s the firmware reports.
4. Pure-Python AES-128-CBC encrypt (about 120 lines, verified against NIST FIPS-197 vector) replaces the PowerShell/openssl shell-out.
5. Windows Task Scheduler + Startup .vbs and the pythonw quirks become `scripts/install-windows.ps1`; Linux gets `scripts/install-linux.sh` writing a `systemd --user` unit.
6. The "chip 8" narrative leaves the page; weak-chip flags stay dynamic; the story becomes `docs/case-study-scbox.md`.
7. Goldshell's UI bundles (downloaded for analysis) do not ship. Facts learned from them go in `docs/firmware-api.md`.
8. The service never logs request URLs: the firmware's login is a GET with the encrypted password in the query string.

## Package layout

```
goldshell-box-tools-productguy/
├── README.md                what and why, 3-command quick start, screenshots
├── LICENSE                  MIT
├── STATUS.md                checkpoint: done / not done / open / next action
├── pyproject.toml           `pipx install .` -> `gbox`
├── .gitignore
├── gbox/
│   ├── __init__.py          __version__
│   ├── aes.py               AES-128-CBC encrypt only, zero IV, zero padding, hex out
│   ├── api.py               login, GET/PUT, parsers; ONE serialized session (a lock), 401 retry
│   ├── config.py            config.json (optional), data dir, permissions
│   ├── poller.py            poll thread -> ~/.gbox/log.csv; COLUMNS append-only; migrate_columns
│   ├── watchdog.py          stall rules, capped soft restarts, the power rung (dry run by default)
│   ├── plug.py              smart plug drivers (Kasa legacy), LAN discovery, the driver interface
│   ├── events.py            the event log (one line per thing the tools did)
│   ├── trials.py            log.csv -> runs per clock and fan target -> table; run_trial (the unattended runner)
│   ├── server.py            static, CSV, events, trials table, trial progress, token hand-off, event line, plug state; 127.0.0.1
│   ├── cli.py               init | status | chips | plan | fantarget | restart | trials [run] | power ... | serve
│   └── web/
│       ├── index.html       works standalone (file://) and served
│       ├── app.js           data layer above a `typeof document` guard (unit-tested under Node), DOM below
│       └── style.css
├── tests/
│   ├── test_aes.py          FIPS-197 vector + the zero-padding contract
│   ├── test_api.py          session lock, 401 retry, re-login
│   ├── test_parsers.py      minerinfo / icinfo / setting fixtures (sanitized)
│   ├── test_poller.py       one sample to CSV, error rows, header migration, config round trip
│   ├── test_watchdog.py     stall detection with a fake clock, caps, gaps; the power rung's five conditions
│   ├── test_plug.py         the Kasa driver against the fake plug: framing, meter shapes, cycle, discovery
│   ├── test_config.py       the optional power block: defaults, validation, round trip
│   ├── test_server.py       routes, no URL logging, bind address, trials endpoints, the health power block
│   ├── test_trials.py       segment boundaries, every column, rollup, the runner with a fake clock
│   ├── test_cli.py          gbox trials, gbox trials run, gbox power ... against the fakes and a real service
│   ├── test_app_js.py       runs app_test.js under Node when Node is present
│   ├── test_models.py       the rated-figures table: loose lookup, unknown models, the JS mirror is identical
│   ├── app_test.js          request builders, event markers, trial row formatting, the service power line, the Power tile, CSV rows, interventions
│   ├── fake_miner.py        HTTP stub serving the fixtures + accepting PUTs; used by tests and by hand
│   ├── fake_plug.py         a Kasa plug on the legacy protocol, with a meter, a relay, and failure knobs
│   └── fixtures/            sanitized SC-BOX captures; sclite/ is synthetic, from the other developer's notes, until a capture replaces it
├── scripts/
│   ├── install-windows.ps1  Startup .vbs launcher (done)
│   ├── install-linux.sh     systemd --user unit (step 3)
│   └── gbox.service         (step 3)
├── docs/
│   ├── plan.md              this file
│   ├── firmware-api.md      endpoints, cipher, quirks, power plan dialects, counters, hashboard behavior seen
│   ├── stock-ui-debug-page.md  the hidden /#/debug page of the stock UI and what each part shows
│   ├── clock-tuning.md      the method: manual and unattended clock trials, reading the table, power
│   ├── power-cycle.md       the smart-plug rung: which plugs, discover, init, dry run, the five conditions, arming
│   ├── power-cycle-proposal.md  the research and design behind it (market, repos, protocols, PDUs, effort)
│   ├── security-notes.md    what the firmware exposes (token, credentials in the API, factory reset); smart plugs
│   ├── capture-request.md   what an owner of another model captures (seven reads, one at a time), redacts and sends, so a fixture can be built
│   ├── case-study-scbox.md  the 2026-09-05 diagnosis and the clock trials that followed (step 4)
│   └── architecture.md      browser / service / miner diagram (step 4)
├── Dockerfile               optional (step 4)
└── SECURITY.md              (step 4; drafts from security-notes.md)
```

## Credential flow

1. User opens the dashboard (file or served). Pop-up asks for the miner password. Page encrypts it (WebCrypto, PKCS#7 trick documented in `firmware-api.md`), logs in, keeps the session token in browser storage.
2. If a service is reachable at the page's origin (`/api/health`), the page POSTs the token to `/api/token`. The service keeps it in memory only and uses it for the logger and watchdog. Standalone page: this step is skipped silently.
3. CLI commands prompt for the password each run, or read `GBOX_PASSWORD`. `gbox serve --remember` is the only path that writes a password to disk; once it has, every CLI command on that machine reads it from `config.json` and does not prompt, which is what lets `gbox trials run` start unattended. `gbox trials` (the table) reads only the log and needs no credentials.
4. The docs say outright: on this firmware the token is deterministic from the password and never expires, so the browser copy is password-equivalent. The stock UI stores the same token the same way.

## Protected buttons (scope)

Clock in 25 MHz steps within the firmware range; fan target 65-75 (the daemon
clamps lower values, verified); soft restart; revert to factory preset.
Each button: a confirm that shows the exact request; password re-entry for
clock and restart; an event-log line on success (with markers on the fan
chart when served). Never press-through on Enter.

As built (2026-09-07): the revert button became a firmware preset picker,
and it asks for the password too, because on this unit a preset is a clock
change to 725 MHz, the dangerous direction. The confirm dialog leads with
one line per changed field and folds the full request under a toggle.
Decided 2026-09-09: the preset picker keeps its password prompt. Every
action that changes the clock asks for the password, with no exception for
the most dangerous one.

## Firmware facts that shape the code

- Login: `GET /user/login?username=admin&password=<hex>&cipher=true`; hex is AES-128-CBC of the password with key `!!!!!!!!!!!!!!!!`, zero IV, zero padding. Returns `{"JWT Token": ...}`.
- Token check has a race: concurrent requests get sporadic 401 ("Check Token Error" in the miner syslog). Send one request at a time, retry a 401 twice, count only then.
- Heavy request bursts (about 15/s) crash the web backend `minerd`; it restarts by itself and loses the hashrate history buffer. Never poll faster than the page does today.
- `/mcb/setting` PUT with `manual:true` and `manualPowerplan:"<MHz> MHz <V> V <fan> RPM <fan> RPM"` sets the clock live. The fan fields are ignored. The stock Miner page's Save always writes `manual:false` (reverts to preset), and that page does expose a fan-target slider, so the trap is real (corrected 2026-09-07 from the page source). `temp_target` is clamped to `temp_targets` (65-75, read-only). `tempcontrol` does not affect fans.
- `/dbg/*` endpoints are big text files regenerated per request; `dbg/fanctrllog` grows to ~1 MB; read it at most once a minute.
- `cpb/hshistory`: 288 samples, one per minute, MH/s, newest last.
- CORS is `Access-Control-Allow-Origin: *`, which is what makes the standalone page possible.
- `Accepted` shares per hour follow the pool's per-connection difficulty as much as the miner. The same unit logged 1521/hr and 643/hr at one clock and one hashrate on two pool sessions. So the clock-trials table carries a hashrate column and treats accepted/hour as "what the pool pays on", not throughput.
- `Hardware Errors` is the sum of the chips' bad nonces. A per-run error rate needs the all-chip good-nonce total, which is why the logger keeps `nonces_good`. Counters reset on a controller restart, so runs break where `Device Elapsed` drops. Details in `firmware-api.md`, "Counters".
- The stock UI has a hidden `/#/debug` page that renders the `/dbg/` endpoints; most owners never find it. `docs/stock-ui-debug-page.md` documents it because it is the fastest way for a user to confirm what the dashboard says without installing anything.

## Order of work (each a session-sized gate)

1. Restructure: package, config, AES, serialized API, single process, fake miner, tests green on Windows against fixtures and against the real unit. Done-when: `gbox serve` replaces the three scheduled tasks on this PC and `python -m unittest` passes.
2. Buttons: built against the fake miner, then the real unit with Mark pressing them. Done-when: all four controls work with confirm + password + event log, standalone and served.
2b. Clock trials, added 2026-09-08 after the 500/550/575/600 MHz experiment. The logger keeps all-chip nonce totals, the fan target, and the overheat flag. `gbox/trials.py` cuts `log.csv` into runs per clock and fan target, breaking on restarts, pool resets, and gaps, and pools them. The dashboard shows the table above the Service section and `gbox trials` prints it. `gbox trials run` steps through a clock list unattended with abort rules and always ends on a safe clock. The runner is a CLI process, not a service thread, so the service never gains a clock-changing endpoint. Method for users in `docs/clock-tuning.md`. Done-when: the table reproduces the hand-built comparison from the experiment; a scripted runner test covers completion, both abort rules, a stale log, and Ctrl-C; and the page shows a running trial's progress. Done 2026-09-08, deployed and run on the real unit the same night.
3. Linux: installer + systemd unit tested on the Ubuntu box or in a container. Both installers must produce the same thing: the same `gbox serve` command line, the same data directory rule (`~/.gbox`), a service that survives logout and reboot, and an uninstall that reverses it. `scripts/install-windows.ps1` is the reference for behavior. Done-when: fresh Ubuntu, three commands, dashboard up, survives logout. **Written 2026-09-13 as `scripts/install-linux.sh` (0.5.2)** with a dry-run test that runs under any bash; the live checklist is in the script's header and is to be run on the Ubuntu 22.04 sibling machine after a push, since the Windows box has neither WSL nor Docker. Not yet verified on Linux as of the write.
4. Docs and release polish. The first commit and push happened 2026-09-06 (0.1.0), and 0.2.0 followed on 2026-09-09. What remains:
   - `docs/case-study-scbox.md`. Every number it needs is in `firmware-api.md`, `clock-tuning.md`, and the STATUS checkpoint of 2026-09-09.
   - `docs/architecture.md`.
   - `SECURITY.md`, drafted from `security-notes.md`.
   - Screenshots. The Clock trials table with the guard-trip row is the one that tells the story.
   - A sanitizing pass.
   - One security review at feature-complete, Mark's standing default. The 2b diff had its own review on 2026-09-08 with no findings.

## Deferred, with the reason (not in scope until Mark says so)

| Item | Why it is not in the plan | What would change the answer |
|---|---|---|
| Token check on `POST /api/event` | loopback-only by default; anyone who can reach the service can already read the miner. A forged log line is the whole exposure. | `--bind` on a LAN becoming the normal setup |
| ~~Hardware watchdog (smart plug cycled on ping loss)~~ | **Built 2026-09-10 as the power rung** (Decisions above, `power-cycle.md`), after the third freeze on 2026-09-09 22:22. "Ping loss" became "unreachable plus two failed soft restarts": standard-library Python cannot send ICMP without administrator rights on any OS, and the TCP connect the watchdog already makes failed at the same moment ping did in every episode. | Follow-ons: Shelly, Tasmota and generic HTTP drivers; KLAP. Watts and GH/s per watt in the trials table: built in 0.4.0. |
| Service hands its token to the page under `--remember` | changes the credential flow above (the page would never ask for a password on a served dashboard) | Mark deciding the convenience is worth the wider token exposure |
| A settings write endpoint in the service | would let a trial be started from the page; rejected in 2b for the security reason in Decisions | never, unless the token check above exists first |
| Runner event lines carry the `dashboard:` prefix | they go through `/api/event`; an `origin` field is a small change | cosmetic; fold into step 4 if convenient |

## User Guide (before the full release to other users)

Asked for by Mark on 2026-09-22 (session AH). The guide gets a **"How it works inside"** section that
explains, with a diagram for each, three interactions:

- **The service and each miner.** What `gbox serve` reads every poll (port 4028 `devs`, `/dbg/icinfo`,
  `/mcb/setting`), what it reads less often (the miner log every `syslog_interval`), what it writes
  (`log.csv`, `minerlog.csv`, `events.log`), and when the watchdog acts.
- **The service and each plug.** What is read every poll, and when the plug is switched.
- **The dashboard and the service.** How the page stays current: `poll` every 10 s reads the miner
  **directly from the browser** (`dbg/minerinfo` and `dbg/icinfo` every time; `mcb/setting`, `mcb/status`
  and `cpb/hshistory` once a minute) and drives the badge, tiles, chip table and hashrate chart;
  `serviceTick` every 60 s reads the service (`api/health`, events, the `log.csv` tail, `api/series`)
  and redraws the history charts, whose right-hand "now" edge moves at each tick.

The guide must say plainly that an open tab is its own client of the miner: the one-request-at-a-time rule
holds inside one tab, not across tabs and the service. Verify the timers against `gbox/web/app.js` when
writing, since they may change before then.

## Stop-losses for the build sessions

- Same file "fixed" three times: stop, write up, fresh diagnosis.
- Never test against the real miner with more than one request in flight or faster than one poll per 10 s.
- Never run a security review pass more than once against unfinished code.
