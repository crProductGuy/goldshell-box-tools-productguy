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
│   └── fixtures/
├── scripts/
│   ├── install-windows.ps1  Startup .vbs launcher (done)
│   ├── install-linux.sh     systemd --user unit (step 3)
│   └── gbox.service         (step 3)
├── docs/
│   ├── plan.md              this file
│   ├── firmware-api.md      endpoints, cipher, quirks, counters, hashboard behavior seen
│   ├── stock-ui-debug-page.md  the hidden /#/debug page of the stock UI and what each part shows
│   ├── clock-tuning.md      the method: manual and unattended clock trials, reading the table, power
│   ├── power-cycle.md       the smart-plug rung: which plugs, discover, init, dry run, the five conditions, arming
│   ├── power-cycle-proposal.md  the research and design behind it (market, repos, protocols, PDUs, effort)
│   ├── security-notes.md    what the firmware exposes (token, credentials in the API, factory reset); smart plugs
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

## Stop-losses for the build sessions

- Same file "fixed" three times: stop, write up, fresh diagnosis.
- Never test against the real miner with more than one request in flight or faster than one poll per 10 s.
- Never run a security review pass more than once against unfinished code.
