#!/usr/bin/env bash
# Start `gbox serve` at boot on a systemd Linux, as your own user, without root.
#
# What it does: writes a systemd *user* unit (~/.config/systemd/user/gbox.service)
# that runs `python3 -m gbox serve` from this clone, enables and starts it, and
# turns on "linger" for your user so the service runs with nobody logged in and
# comes back after a reboot. Then it asks the service for its health line.
# This is the Linux twin of scripts/install-windows.ps1: same command line,
# same data directory rule (~/.gbox unless --data), same uninstall.
#
# Three commands on a fresh box with Python 3.8 or newer:
#
#     git clone https://github.com/crProductGuy/goldshell-box-tools-productguy.git && cd goldshell-box-tools-productguy
#     python3 -m gbox init                       # the miner's address; add `python3 -m gbox serve --remember` for a password on disk
#     scripts/install-linux.sh                   # --uninstall reverses it; --dry-run prints every step and touches nothing
#
# Verification checklist (the done-when in docs/plan.md, step 3), on a real box:
#   1. the three commands above print "gbox <version> is up: http://127.0.0.1:8765/"
#   2. the dashboard opens in a browser on that box and shows the miner
#   3. log out and back in: `systemctl --user status gbox` still says active
#   4. reboot: the dashboard is up again with nobody logged in (linger)
#   5. `scripts/install-linux.sh --uninstall`: the unit is gone and the port is closed
#
# Options:
#   --data DIR     data directory to pass as --data (default: gbox's own, ~/.gbox)
#   --python PATH  the Python to run (default: python3 on PATH)
#   --uninstall    stop and disable the service, remove the unit
#   --dry-run      print the unit and the commands; change nothing
#
# Linger: `loginctl enable-linger` for your own user works without root on most
# distributions. Where polkit refuses it, the script prints the sudo form and
# carries on; the service still runs while you are logged in.
set -euo pipefail

usage() {
  echo "usage: scripts/install-linux.sh [--data DIR] [--python PATH] [--uninstall] [--dry-run]" >&2
  exit 2
}

DATA_DIR=""
PYTHON=""
UNINSTALL=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --data) [ $# -ge 2 ] || usage; DATA_DIR="$2"; shift 2 ;;
    --python) [ $# -ge 2 ] || usage; PYTHON="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/gbox.service"

run() {
  # Print every command; run it unless this is a dry run.
  echo "+ $*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry run: nothing below is executed or written"
elif ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found: this script needs a systemd Linux (Ubuntu, Debian, Fedora, ...)." >&2
  echo "On another init system, run 'python3 -m gbox serve' from this directory under your init's supervisor." >&2
  exit 1
fi

if [ "$UNINSTALL" -eq 1 ]; then
  run systemctl --user disable --now gbox.service || true
  run rm -f "$UNIT"
  run systemctl --user daemon-reload
  echo "gbox service removed (linger left as it was; 'loginctl disable-linger' turns it off)"
  exit 0
fi

if [ -z "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "python3 not found on PATH. Install Python 3.8 or newer (for example: sudo apt install python3)." >&2
  exit 1
fi
if [ "$DRY_RUN" -eq 0 ] && ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "$PYTHON is older than 3.8; gbox needs 3.8 or newer." >&2
  exit 1
fi

EXEC="$PYTHON -m gbox serve"
if [ -n "$DATA_DIR" ]; then
  EXEC="$EXEC --data $DATA_DIR"
fi

UNIT_TEXT="[Unit]
Description=gbox: Goldshell Box dashboard, logger and watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$EXEC
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target"

echo "--- $UNIT ---"
echo "$UNIT_TEXT"
echo "--- end of unit ---"
run mkdir -p "$UNIT_DIR"
if [ "$DRY_RUN" -eq 0 ]; then
  printf '%s\n' "$UNIT_TEXT" > "$UNIT"
  echo "wrote $UNIT"
fi
run systemctl --user daemon-reload
run systemctl --user enable --now gbox.service
ME="${USER:-$(id -un)}"
if ! run loginctl enable-linger "$ME"; then
  echo "linger refused for $ME: the service runs while you are logged in. To keep it running with nobody" >&2
  echo "logged in and after a reboot, run once:  sudo loginctl enable-linger $ME" >&2
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "+ (health check of http://127.0.0.1:<port>/api/health after 3 s)"
  exit 0
fi

sleep 3
CFG="${DATA_DIR:-$HOME/.gbox}/config.json"
PORT=8765
if [ -f "$CFG" ]; then
  PORT="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8765))' "$CFG" 2>/dev/null || echo 8765)"
fi
if "$PYTHON" - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/health" % port, timeout=5) as r:
        h = json.load(r)
except Exception as e:
    sys.exit(1)
print("gbox %s is up: http://127.0.0.1:%s/  (miner %s, poll every %s s)" % (h.get("version"), port, h.get("host"), h.get("poll_interval")))
PY
then :; else
  echo "the service did not answer on port $PORT yet. Look with:  systemctl --user status gbox   or run  python3 -m gbox serve  in a terminal to see why." >&2
fi
