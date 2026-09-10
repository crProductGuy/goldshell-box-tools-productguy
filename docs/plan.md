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
│   ├── watchdog.py          stall rules, capped soft restarts
│   ├── events.py            the event log (one line per thing the tools did)
│   ├── trials.py            log.csv -> runs per clock and fan target -> table; run_trial (the unattended runner)
│   ├── server.py            static, CSV, events, trials table, trial progress, token hand-off, event line; 127.0.0.1
│   ├── cli.py               init | status | chips | plan | fantarget | restart | trials [run] | serve
│   └── web/
│       ├── index.html       works standalone (file://) and served
│       ├── app.js           data layer above a `typeof document` guard (unit-tested under Node), DOM below
│       └── style.css
├── tests/
│   ├── test_aes.py          FIPS-197 vector + the zero-padding contract
│   ├── test_api.py          session lock, 401 retry, re-login
│   ├── test_parsers.py      minerinfo / icinfo / setting fixtures (sanitized)
│   ├── test_poller.py       one sample to CSV, error rows, header migration, config round trip
│   ├── test_watchdog.py     stall detection with a fake clock, caps, gaps
│   ├── test_server.py       routes, no URL logging, bind address, trials endpoints
│   ├── test_trials.py       segment boundaries, every column, rollup, the runner with a fake clock
│   ├── test_cli.py          gbox trials and gbox trials run against the fake miner and a real service
│   ├── test_app_js.py       runs app_test.js under Node when Node is present
│   ├── app_test.js          request builders, event markers, trial row formatting
│   ├── fake_miner.py        HTTP stub serving the fixtures + accepting PUTs; used by tests and by hand
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
│   ├── security-notes.md    what the firmware exposes (token, credentials in the API, factory reset)
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
3. Linux: installer + systemd unit tested on the Ubuntu box or in a container. Both installers must produce the same thing: the same `gbox serve` command line, the same data directory rule (`~/.gbox`), a service that survives logout and reboot, and an uninstall that reverses it. `scripts/install-windows.ps1` is the reference for behavior. Done-when: fresh Ubuntu, three commands, dashboard up, survives logout.
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
| Hardware watchdog (smart plug cycled on ping loss) | a frozen controller defeats the software watchdog; the fix is outside the software. **The second freeze happened 2026-09-09 19:00** (off the wire for nearly three hours, six restarts timed out, 53 W at the wall, cleared by a power cycle), so the trigger named here has fired. | Mark's call on scope; a smart plug the service can toggle on ping loss is the shape of it |
| Service hands its token to the page under `--remember` | changes the credential flow above (the page would never ask for a password on a served dashboard) | Mark deciding the convenience is worth the wider token exposure |
| A settings write endpoint in the service | would let a trial be started from the page; rejected in 2b for the security reason in Decisions | never, unless the token check above exists first |
| Runner event lines carry the `dashboard:` prefix | they go through `/api/event`; an `origin` field is a small change | cosmetic; fold into step 4 if convenient |

## Stop-losses for the build sessions

- Same file "fixed" three times: stop, write up, fresh diagnosis.
- Never test against the real miner with more than one request in flight or faster than one poll per 10 s.
- Never run a security review pass more than once against unfinished code.
