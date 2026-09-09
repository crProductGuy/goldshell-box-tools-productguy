# Plan: goldshell-box-tools

Decided with Mark on 2026-09-05. This file is the design of record for the
public release. `STATUS.md` at the repo root tracks progress against it.

## Goal

A public, MIT-licensed toolkit at github.com/crProductGuy/goldshell-box-tools
that lets a somewhat-technical owner of a Goldshell Box-series miner see what
the stock UI hides, change the settings the stock UI cannot reach, and keep
the miner hashing unattended. Install must be easy on Ubuntu-class Linux and
on Windows: standard-library Python only, one process, one command.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Repo name | `goldshell-box-tools`, command `gbox` | findability; other Box models share the firmware |
| License | MIT | Bitcoin Core's license; shortest; no obligations on forks. GPLv3 was the alternative, rejected for a gift. Apache-2.0 adds a patent grant nothing here needs. |
| Secrets | none on disk by default | user preference. The dashboard pop-up is the only place a password is typed. Opt-in `--remember` stores it in `config.json` with owner-only permissions for unattended reboots, and the README says what that trades away. |
| Standalone page | yes | `gbox/web/index.html` must work opened as a file with no service: live status and the read-only view. The service adds the fan/temperature history, the event log and reboot survival. |
| Dependencies | Python standard library only | install = clone (or `pipx install .`) and run; no compiler, no pip resolution failures for near-normies |
| Process model | one process, `gbox serve` | logger thread + watchdog thread + local HTTP server; one thing to start and stop |
| Bind address | 127.0.0.1 by default | anyone who can open the page can press the buttons; LAN exposure is an explicit flag with a warning |
| Source of truth for the current running tools | `Projects/scbox-tools/` (not a repo) | keep it running untouched while the package is built; port from it, do not move it |

## What must change from `scbox-tools`

1. Secrets and personal data out: `pw.hex`, CSV/logs, miner-syslog copies (carry the pool wallet), `setting_before.json` (carries the unit's MAC). All runtime state under `data/`, gitignored.
2. No hard-coded miner address, nominal hashrate, poll intervals: config + prompts.
3. Chip count and board count read from the miner (`icinfo.drawdata` is an array of boards). Hashrate auto-scaled from the MH/s the firmware reports.
4. Pure-Python AES-128-CBC encrypt (about 120 lines, verified against NIST FIPS-197 vector) replaces the PowerShell/openssl shell-out.
5. Windows Task Scheduler + Startup .vbs and the pythonw quirks become `scripts/install-windows.ps1`; Linux gets `scripts/install-linux.sh` writing a `systemd --user` unit.
6. The "chip 8" narrative leaves the page; weak-chip flags stay dynamic; the story becomes `docs/case-study-scbox.md`.
7. Goldshell's UI bundles (downloaded for analysis) do not ship. Facts learned from them go in `docs/firmware-api.md`.
8. The service never logs request URLs: the firmware's login is a GET with the encrypted password in the query string.

## Package layout

```
goldshell-box-tools/
├── README.md                what and why, 3-command quick start, screenshots
├── LICENSE                  MIT
├── STATUS.md                checkpoint: done / not done / open / next action
├── pyproject.toml           `pipx install .` -> `gbox`
├── .gitignore
├── gbox/
│   ├── __init__.py
│   ├── aes.py               AES-128-CBC encrypt only, zero IV, zero padding, hex out
│   ├── api.py               login, GET/PUT, parsers; ONE serialized session (a lock), 401 retry
│   ├── config.py            config.json (optional), data dir, permissions
│   ├── logger.py            poll thread -> data/log.csv (same columns as today)
│   ├── watchdog.py          stall rules, capped soft restarts, event log
│   ├── server.py            static, CSV, events, token hand-off endpoint; 127.0.0.1
│   ├── cli.py               init | status | chips | plan | fantarget | restart | serve
│   └── web/
│       ├── index.html       works standalone (file://) and served
│       ├── app.js
│       └── style.css
├── tests/
│   ├── test_aes.py          FIPS-197 vector + the zero-padding contract
│   ├── test_parsers.py      minerinfo / icinfo / setting fixtures (sanitized)
│   ├── test_watchdog.py     stall detection with a fake clock, caps, gaps
│   ├── test_server.py       routes, no URL logging, bind address
│   ├── fake_miner.py        HTTP stub serving the fixtures + accepting PUTs; used by tests and by hand
│   └── fixtures/
├── scripts/
│   ├── install-linux.sh
│   ├── gbox.service
│   └── install-windows.ps1
├── docs/
│   ├── plan.md              this file
│   ├── firmware-api.md      endpoints, cipher, quirks (dead manual field, target clamp, token race, fan fields ignored)
│   ├── case-study-scbox.md  the 2026-09-05 diagnosis
│   └── architecture.md      browser / service / miner diagram
├── Dockerfile               optional
└── SECURITY.md
```

## Credential flow

1. User opens the dashboard (file or served). Pop-up asks for the miner password. Page encrypts it (WebCrypto, PKCS#7 trick documented in `firmware-api.md`), logs in, keeps the session token in browser storage.
2. If a service is reachable at the page's origin (`/api/health`), the page POSTs the token to `/api/token`. The service keeps it in memory only and uses it for the logger and watchdog. Standalone page: this step is skipped silently.
3. CLI commands prompt for the password each run. `gbox serve --remember` is the only path that writes a password to disk.
4. Honest note for docs: on this firmware the token is deterministic from the password and never expires, so the browser copy is password-equivalent. The stock UI stores the same token the same way.

## Protected buttons (scope)

Clock in 25 MHz steps within the firmware range; fan target 65-75 (the daemon
clamps lower values, verified); soft restart; revert to factory preset.
Each button: a confirm that shows the exact request; password re-entry for
clock and restart; an event-log line on success (with markers on the fan
chart when served). Never press-through on Enter.

## Firmware facts that shape the code

- Login: `GET /user/login?username=admin&password=<hex>&cipher=true`; hex is AES-128-CBC of the password with key `!!!!!!!!!!!!!!!!`, zero IV, zero padding. Returns `{"JWT Token": ...}`.
- Token check has a race: concurrent requests get sporadic 401 ("Check Token Error" in the miner syslog). Send one request at a time, retry a 401 twice, count only then.
- Heavy request bursts (about 15/s) crash the web backend `minerd`; it restarts by itself and loses the hashrate history buffer. Never poll faster than the page does today.
- `/mcb/setting` PUT with `manual:true` and `manualPowerplan:"<MHz> MHz <V> V <fan> RPM <fan> RPM"` sets the clock live. The fan fields are ignored. The stock Miner page's Save always writes `manual:false` (reverts to preset), and that page does expose a fan-target slider, so the trap is real (corrected 2026-09-07 from the page source). `temp_target` is clamped to `temp_targets` (65-75, read-only). `tempcontrol` does not affect fans.
- `/dbg/*` endpoints are big text files regenerated per request; `dbg/fanctrllog` grows to ~1 MB; read it at most once a minute.
- `cpb/hshistory`: 288 samples, one per minute, MH/s, newest last.
- CORS is `Access-Control-Allow-Origin: *`, which is what makes the standalone page possible.

## Order of work (each a session-sized gate)

1. Restructure: package, config, AES, serialized API, single process, fake miner, tests green on Windows against fixtures and against the real unit. Done-when: `gbox serve` replaces the three scheduled tasks on this PC and `python -m unittest` passes.
2. Buttons: built against the fake miner, then the real unit with Mark pressing them. Done-when: all four controls work with confirm + password + event log, standalone and served.
2b. Clock trials (added 2026-09-08 after the 500/550/575/600 MHz experiment): the logger keeps all-chip nonce totals, fan target and the overheat flag; `gbox/trials.py` cuts `log.csv` into runs per clock and fan target (breaking on restarts, pool resets and gaps) and pools them; the dashboard shows the table above the Service section and `gbox trials` prints it; `gbox trials run` steps through a clock list unattended with abort rules and always ends on a safe clock. The runner is a CLI process, not a service thread, so the service never gains a clock-changing endpoint. Method for users in `docs/clock-tuning.md`. Done-when: the table reproduces the hand-built comparison from the experiment, a scripted runner test covers completion, both abort rules, stale log and Ctrl-C, and the page shows a running trial's progress.
3. Linux: installer + systemd unit tested on the Ubuntu box or in a container. Done-when: fresh Ubuntu, three commands, dashboard up, survives logout.
4. Docs, sanitizing pass, screenshots, one security review at feature-complete (Mark's standing default), then the first commit with README, LICENSE and .gitignore together, and push to crProductGuy.

## Stop-losses for the build sessions

- Same file "fixed" three times: stop, write up, fresh diagnosis.
- Never test against the real miner with more than one request in flight or faster than one poll per 10 s.
- Never run a security review pass more than once against unfinished code.
