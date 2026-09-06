# goldshell-box-tools

Status: pre-release, under construction. See `STATUS.md` and `docs/plan.md`.

A small, dependency-free toolkit for Goldshell Box-series miners (SC-BOX,
KD-BOX, HS-BOX, LT-BOX and relatives running the "cloud-box" MCB_V5 firmware):

- a status dashboard that shows what the stock web UI hides: real chip
  temperature, per-chip health, the board reset counter, fan duty, hashrate,
  fan and temperature history
- protected buttons for the settings the stock UI cannot reach (manual clock,
  fan target, restart, revert to factory) — coming in the next step; today
  those are CLI commands
- a logger and a watchdog that soft-restarts the miner when it stops hashing
- a command line: `gbox status | chips | plan | fantarget | restart | serve`

Born from a diagnosis of an SC-BOX running at 30 percent: one marginal chip was
resetting the whole board every nine seconds at the factory clock, and the
stock UI offered no way to see it or to lower the clock. The story is in
`docs/case-study-scbox.md`.

## Quick start

Python 3.8 or newer, nothing else. Clone, then:

```
python -m gbox init                 # miner address, poll interval -> ~/.gbox/config.json
python -m gbox status               # prompts for the miner's web UI password
python -m gbox serve                # dashboard at http://127.0.0.1:8765/
```

Or `pipx install .` to get a `gbox` command on your PATH.

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
- Linux: `scripts/install-linux.sh` (systemd user unit) — next step of the plan

## Rules the tools follow, and you should too

- One request in flight to the miner at a time, and never more than one poll
  cycle per 10 seconds. The firmware's token check has a race and its web
  backend crashes under bursts. Details in `docs/firmware-api.md`.
- Never press Save on the stock UI's Miner page. Its save handler clears the
  manual power plan and returns the miner to the factory preset.
- The service listens on 127.0.0.1 only unless you pass `--bind`. Anyone who
  can open the page can read the miner and, once the buttons exist, press
  them.

## Development

```
python -m unittest                  # fixtures + a fake miner, no hardware needed
python -m tests.fake_miner          # a fake miner on :8999, password "password"
python -m gbox --host 127.0.0.1:8999 --data /tmp/gbox-dev serve --port 8770
```

License: MIT.
