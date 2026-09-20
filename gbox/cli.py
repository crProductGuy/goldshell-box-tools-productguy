"""The gbox command line.

    gbox discover                  find Goldshell miners on the local network, no password needed
    gbox init                      write config.json (miner address, poll interval)
    gbox status                    hashrate, errors, fans, temperatures, reset counter
    gbox chips                     per-chip good/bad nonce table with weak/failing flags
    gbox plan 600                  set the clock (25 MHz steps) via the manual power plan
    gbox fantarget 65              fan controller target on the board sensor
    gbox restart                   soft restart
    gbox trials                    error and throughput per clock, from the service log
    gbox errors                    bad share, worst chip, clock and resets per half hour, from the service log
    gbox power discover|init|status|cycle|off|on
                                   a smart plug that can cut power to a frozen controller,
                                   and switch the miner off and on by hand
    gbox hold [MINUTES|release]    tell the running service the miner will be unreachable on
                                   purpose, so the watchdog stands down until it is back
    gbox serve                     dashboard + logger + watchdog in one process

Commands that talk to the miner prompt for the web UI password unless the
GBOX_PASSWORD environment variable is set or `gbox serve --remember` stored
it. `gbox serve` without a stored password waits for the dashboard to hand
over a session token after you log in there.
"""
import argparse
import getpass
import os
import sys
import threading

from . import __version__, api, config, discover as discovermod, pidfile, plug as plugmod, series, trials
from .events import EventLog
from .poller import COLUMNS, Poller, migrate_columns
from .power import PowerControl, Scheduler
from .server import ServiceState, make_server
from .watchdog import Watchdog


def _printable(text, stream):
    """`text` with whatever the stream's encoding cannot carry replaced.

    A model string comes off the miner, and `gbox discover` reads them off units nobody here has
    seen. The SC5 Pro II's is "Goldshell-SC5Pro" + U+2161 and a Windows console is cp1252, so
    printing it raised UnicodeEncodeError, which is a ValueError, which main() turned into
    `gbox: 'charmap' codec ...` and exit 1. A question mark beats losing the command."""
    enc = getattr(stream, "encoding", None) or "utf-8"
    return str(text).encode(enc, "replace").decode(enc, "replace")


def _out(*a):
    try:
        print(*a)
    except UnicodeEncodeError:
        print(*[_printable(x, sys.stdout) for x in a])


_DISCOVER_ROW = "%-21s %-20s %-20s %-9s %s"


def _die(msg, code=1):
    try:
        print("gbox: " + str(msg), file=sys.stderr)
    except UnicodeEncodeError:
        print("gbox: " + _printable(msg, sys.stderr), file=sys.stderr)
    sys.exit(code)


def _fmt_hash(mhs):
    unit, div = api.hashrate_unit(mhs)
    return "-" if mhs is None else "%.1f %s" % (mhs / div, unit)


def _password(prompt="miner web UI password: "):
    env = os.environ.get("GBOX_PASSWORD")
    if env:
        return env
    if not sys.stdin or not sys.stdin.isatty():
        _die("no password: set GBOX_PASSWORD or run interactively")
    return getpass.getpass(prompt)


def _miner(args, cfg, need_password=True):
    host = args.host or cfg.host
    if not host:
        _die("no miner address: run `gbox init` or pass --host")
    if cfg.password_hex:
        return api.Miner(host, password_hex=cfg.password_hex)
    if need_password:
        return api.Miner(host, password=_password())
    return api.Miner(host)


# ---------------------------------------------------------------- commands

def cmd_discover(args, cfg, data_dir):
    """One tokenless GET /mcb/status per address on the subnet. No password, no login, no settings read."""
    if args.target:
        targets, where = list(args.target), "%d given address%s" % (len(args.target), "" if len(args.target) == 1 else "es")
    else:
        where = args.subnet or discovermod.local_subnet()
        targets = discovermod.targets_for(where)          # a bad or too-wide subnet raises ValueError: main exits 1

    configured = cfg.host or ""
    skipping = bool(configured) and not args.include_configured
    if configured and args.include_configured:
        _out("probing the configured miner as well; do this only while `gbox serve` is stopped,")
        _out("because the firmware answers one caller at a time")

    hits = discovermod.sweep(targets, timeout=args.timeout, skip=(configured,) if skipping else ())

    if hits:
        _out(_DISCOVER_ROW % ("address", "model", "name", "firmware", "hardware"))
        for h in hits:
            _out(_DISCOVER_ROW % (h["address"], h["model"], h["name"], h["firmware"] or "?", h["hardware"] or "?"))
    if skipping:
        _out("%-21s configured, not probed (--include-configured asks it too)" % configured)
    if not hits:
        _out("no %sGoldshell miner answered in %s" % ("other " if skipping else "", where))
        _out("(a miner on another subnet needs --subnet; one that is off or still booting will not answer)")
        sys.exit(1)
    if not cfg.host:
        _out("")
        _out("next: gbox init --host %s" % hits[0]["address"])


def cmd_init(args, cfg, data_dir):
    def ask(label, current):
        if not sys.stdin or not sys.stdin.isatty():
            return current
        try:
            v = input("%s [%s]: " % (label, current)).strip()
        except EOFError:
            return current
        return v or current

    cfg.host = args.host or ask("miner address (IP or hostname)", cfg.host or "192.168.1.100")
    cfg.poll_interval = args.interval or int(ask("poll interval in seconds (min %d)" % config.MIN_POLL_INTERVAL, cfg.poll_interval))
    cfg.validate()
    path = config.save(cfg, data_dir)
    _out("wrote", path)
    _out("next: `gbox status` to check the connection, `gbox serve` for the dashboard")


def cmd_status(args, cfg, data_dir):
    m = _miner(args, cfg)
    st, info, setting = m.status(), m.minerinfo(), m.setting()
    plan = setting.get("manualPowerplan") if setting.get("manual") else "preset %s" % setting.get("select")
    up = info["elapsed"] or 0
    rows = [
        ("model", "%s  hw %s  fw %s  %s" % (st.get("model"), st.get("hardware"), st.get("firmware"), st.get("mcbversion"))),
        ("uptime", "%dh %02dm" % (up // 3600, up % 3600 // 60)),
        ("plan", "%s (manual=%s)" % (plan, setting.get("manual"))),
        ("clock", "%s MHz" % info["clock"]),
        ("hashrate 20s", _fmt_hash(info["mhs_20s"])),
        ("hashrate avg", _fmt_hash(info["mhs_av"])),
        ("shares", "%s accepted, %s rejected" % (info["accepted"], info["rejected"])),
        ("hw errors", "%s (%s %%)" % (info["hw_errors"], info["hw_pct"])),
        ("chip temp", "%s C (board sensor %s C, fan target %s C)" % (info["chip_temp"], info["board_temp"], setting.get("temp_target"))),
        ("fans", "%s / %s RPM" % (info["fan0"], info["fan1"])),
        ("board resets", info["rebootcnt"]),
        ("overheat flag", info["overheat"]),
    ]
    for k, v in rows:
        _out("%-14s %s" % (k, v))


def cmd_chips(args, cfg, data_dir):
    boards = _miner(args, cfg).boards()
    for b, board in enumerate(boards):
        best = max(c["good"] for c in board) if board else 0
        if len(boards) > 1:
            _out("board %d" % b)
        _out("%5s %8s %6s %6s  %s" % ("chip", "good", "bad", "bad%", "health"))
        for c in board:
            total = c["good"] + c["bad"]
            pct = 100.0 * c["bad"] / total if total else 0.0
            health = api.chip_health(c, best)
            _out("%5d %8d %6d %5.1f%%  %s" % (c["chip"], c["good"], c["bad"], pct, "" if health == "ok" else health))


def cmd_plan(args, cfg, data_dir):
    m = _miner(args, cfg)
    applied = m.set_plan(args.mhz, args.volts)
    _out("applied:", applied)
    _out("The stock Miner page will still say Hashrate Mode. Never press Save there: it reverts to the preset.")


def cmd_fantarget(args, cfg, data_dir):
    _out("fan target now %s C" % _miner(args, cfg).set_fan_target(args.temp))
    _out("The fans spike for a few minutes while the fan daemon restarts; judge the result after that.")


def cmd_restart(args, cfg, data_dir):
    _miner(args, cfg).restart()
    _out("restart sent; the miner takes about 60-90 s to come back and start hashing")


def _service_url(cfg):
    host = "127.0.0.1" if cfg.bind in ("0.0.0.0", "", "127.0.0.1", "localhost") else cfg.bind
    return "http://%s:%d" % (host, cfg.port)


def cmd_trials_run(args, cfg, data_dir):
    import json
    import urllib.request
    url = _service_url(cfg)
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=5) as r:
            health = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _die("the runner judges by the service's log, so `gbox serve` must be running (nothing answered at %s: %s)" % (url, e))
    if not (health.get("has_token") or health.get("can_login")):
        _die("the service at %s is not logging yet: log in on the dashboard first, or restart it with --remember" % url)
    warned = []

    def report(text):
        body = json.dumps({"message": text}).encode("utf-8")
        req = urllib.request.Request(url + "/api/event", data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as e:
            if not warned:
                warned.append(e)
                _out("(could not write to the service event log: %s)" % e)

    miner = _miner(args, cfg)
    _out("trial: %s MHz, %g h each%s, ending at %s MHz; the service at %s keeps the log. Ctrl-C stops the run and applies the end clock." % (
        " -> ".join(str(c) for c in args.clocks), args.hours, (", fan target %d C" % args.fan) if args.fan is not None else "",
        args.end if args.end is not None else min(args.clocks), url))
    result = trials.run_trial(
        miner, args.clocks, args.hours, end=args.end, fan=args.fan, settle_min=args.settle, check_min=args.check,
        min_judge_min=args.judge, max_resets=args.max_resets, max_bad=args.max_bad,
        log_path=data_dir / "log.csv", status_path=data_dir / "trial.json", report=report, out=_out)
    _out("now compare the rows: `gbox trials`, or the Clock trials table on the dashboard")
    if not result["completed"]:
        sys.exit(2)


def cmd_trials(args, cfg, data_dir):
    if getattr(args, "trials_cmd", None) == "run":
        return cmd_trials_run(args, cfg, data_dir)
    path = data_dir / "log.csv"
    t = trials.table(path, min_minutes=args.min)
    if not t["segments"]:
        _out("no samples in %s: run `gbox serve` and log in on the dashboard, then hold each clock for a while" % path)
        return
    _out(trials.format_table(t, segments=args.segments))
    _out()
    hidden = sum(1 for s in t["segments"] if s["short"])
    if hidden and not args.segments:
        _out("(%d segment%s under %d min hidden; --segments lists segments, --min changes the cutoff)" % (
            hidden, "" if hidden == 1 else "s", args.min))
    _out("Compare bad share, not bad count: a slower clock attempts fewer nonces. Accepted/hour follows the pool's")
    _out("share difficulty as much as the miner; use hashrate for throughput. HW% with ~ is the firmware's running")
    _out("average, from rows logged by versions before 0.2.0.")


# ---------------------------------------------------------------- power (smart plug)

def _ask_yes(prompt):
    """True for y/yes at a terminal. Dies without a terminal: unattended runs must pass --yes."""
    if not sys.stdin or not sys.stdin.isatty():
        _die("no terminal to confirm on: pass --yes if this is the miner's plug")
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _service_health(cfg):
    """The running service's /api/health, or None when nothing answers."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(_service_url(cfg) + "/api/health", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _service_event(cfg, text):
    """Write one line to the service's event log. Returns True when the service took it."""
    import json
    import urllib.request
    body = json.dumps({"message": text}).encode("utf-8")
    req = urllib.request.Request(_service_url(cfg) + "/api/event", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except Exception:
        return False


def _service_post(cfg, path, body):
    """POST JSON to the running service. Returns (status, parsed body or None); (None, None) when nothing answers."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(_service_url(cfg) + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except ValueError:
            return e.code, None
    except Exception:
        return None, None


def _service_hold(cfg, minutes, reason):
    """Ask the running service for a hold (docs/power-hold-proposal.md). Returns one line for the terminal."""
    status, body = _service_post(cfg, "/api/hold", {"minutes": minutes, "reason": reason})
    if status == 200:
        return "the service holds the watchdog (%s) until the miner hashes twice in a row" % (
            "no expiry" if minutes is None else "%d min" % minutes)
    if status is None:
        return "(service not reachable: no hold, and nothing was logged)"
    return "the service refused the hold: %s" % (body.get("error") if isinstance(body, dict) else "HTTP %s" % status)


def _confirm_word(word):
    """Ask for a typed word on a terminal; die otherwise. Nothing is sent unless it matches."""
    if not sys.stdin or not sys.stdin.isatty():
        _die("no terminal to confirm on; nothing sent")
    try:
        typed = input("type %s to confirm: " % word).strip()
    except EOFError:
        typed = ""
    if typed != word:
        _die("not confirmed; nothing sent")


def _configured_plug(cfg):
    if cfg.power is None:
        _die("no plug configured: `gbox power discover` to find it, then `gbox power init --plug <address>`")
    return plugmod.make(cfg.power)


def _plug_checked(cfg):
    """The configured plug and its identity, or die when it is not the recorded device."""
    p = _configured_plug(cfg)
    info = p.identify()
    if info["device_id"] != cfg.power.get("device_id"):
        _die("the plug at %s is not the device id recorded by `gbox power init`; nothing sent" % cfg.power["host"])
    return p, info


def _fmt_watts(w):
    return ("%.0f W" % w) if w is not None else "no meter"


def cmd_errors(args, cfg, data_dir):
    """The charts' buckets as text: what the errors chart shows, from the log alone (docs/charts-proposal.md)."""
    if not (series.MIN_HOURS <= args.hours <= series.MAX_HOURS):
        _die("--hours must be %d to %d" % (series.MIN_HOURS, series.MAX_HOURS))
    if not (series.MIN_BUCKET <= args.bucket <= series.MAX_BUCKET):
        _die("--bucket must be %d to %d minutes" % (series.MIN_BUCKET, series.MAX_BUCKET))
    log = data_dir / "log.csv"
    rows = series.read_rows(log)
    if not rows:
        _out("no log at %s: the service writes it (gbox serve)" % log)
        return
    events = EventLog(data_dir / "events.log").tail(4000)
    _out(series.format_table(series.buckets(rows, args.hours, args.bucket, events=events)))
    _out("")
    _out("Bad share is bad nonces over all nonces in the bucket, summed from the log's counters across boots; the worst chip is board.chip.")


def cmd_power_discover(args, cfg, data_dir):
    found = plugmod.discover(timeout=args.timeout, port=args.port, targets=tuple(args.target) if args.target else None)
    if not found:
        _out("none found (plugs on the newer KLAP firmware answer discovery but cannot be driven yet;")
        _out("a plug on another subnet is not found by broadcast: try `gbox power init --plug <address>`)")
        return
    _out("%-16s %-12s %-24s %-5s %-9s %s" % ("address", "model", "name", "relay", "meter", "protocol"))
    for d in found:
        relay = {True: "on", False: "off", None: "?"}[d["relay"]]
        meter = _fmt_watts(d["watts"]) if d["meter"] else "no"
        _out("%-16s %-12s %-24s %-5s %-9s %s" % (d["host"], d["model"], d["alias"][:24], relay, meter, d["protocol"]))
    if any(d["protocol"] != "legacy" for d in found):
        _out("(a %s plug needs the TP-Link account credentials; this version drives legacy-protocol plugs only)"
             % ", ".join(sorted({d["protocol"] for d in found if d["protocol"] != "legacy"})))


def cmd_power_init(args, cfg, data_dir):
    p = plugmod.make({"driver": args.driver, "host": args.plug})
    info = p.identify()
    state = p.state()
    w = p.watts() if info["meter"] else None
    _out("%s '%s' (hw %s, fw %s): relay %s, %s" % (info["model"], info["alias"], info["hw"], info["fw"],
                                                    "on" if state else "off", _fmt_watts(w)))
    if not info["meter"]:
        _out("(no energy meter on this model: the watchdog will not see the miner's draw, only the relay)")
    if not args.yes and not _ask_yes("Is this the plug the miner is powered from? [y/N] "):
        _die("nothing written")
    block = dict(cfg.power or {})
    block.update({"driver": args.driver, "host": args.plug, "device_id": info["device_id"]})
    block.setdefault("cycle", False)
    cfg.power = block
    cfg.validate()
    path = config.save(cfg, data_dir)
    _out("wrote", path)
    _out("power block %s: the watchdog logs 'would cycle' and does nothing until you set \"cycle\": true"
         % ("stays in dry run" if not cfg.power["cycle"] else "is ARMED"))
    _out("restart `gbox serve` to pick it up; `gbox power status` shows the plug any time")


def cmd_power_status(args, cfg, data_dir):
    p = _configured_plug(cfg)
    info = p.identify()
    matches = info["device_id"] == cfg.power.get("device_id")
    w = p.watts() if info["meter"] else None
    _out("plug: %s '%s' at %s, relay %s, %s%s" % (info["model"], info["alias"], cfg.power["host"],
                                                  "on" if p.state() else "off", _fmt_watts(w),
                                                  "" if matches else "  ** DEVICE ID DOES NOT MATCH config.json: not the plug that was set up **"))
    _out("mode: %s (after %d min unreachable and two failed soft restarts; %d s off; at most %d cycles a day)" % (
        "ARMED" if cfg.power.get("cycle") else "dry run", cfg.power["after_minutes"], cfg.power["off_seconds"],
        cfg.power["max_cycles_per_day"]))
    h = _service_health(cfg)
    if h is None:
        _out("service not running (or not at %s): cycles in 24 h unknown" % _service_url(cfg))
    else:
        pw = h.get("power") or {}
        _out("service: %d cycles in 24 h%s" % (pw.get("cycles_today", 0),
                                              (", last: " + pw["last_reason"]) if pw.get("last_reason") else ""))


def cmd_power_cycle(args, cfg, data_dir):
    off_seconds = args.off_seconds if args.off_seconds is not None else cfg.power["off_seconds"] if cfg.power else 15
    if not 3 <= off_seconds <= 120:
        _die("--off-seconds must be 3 to 120")
    p, info = _plug_checked(cfg)
    w = p.watts() if info["meter"] else None
    _out("about to cut power to %s '%s' for %d s. It reads %s now%s." % (
        info["model"], info["alias"], off_seconds, _fmt_watts(w),
        "" if w is None or w < cfg.power["idle_watts"] else " (that looks like a miner hashing, not a hung one)"))
    _confirm_word("CYCLE")
    hold = _service_hold(cfg, cfg.power["settle_minutes"], "power cycle by hand")
    p.cycle(off_seconds)
    line = "power: cycled by hand (gbox power cycle; %s before)" % _fmt_watts(w)
    _out("cycled: off %d s, then on. The SC-BOX is back hashing in about a minute (60 to 66 s measured); allow two or three on other units." % off_seconds)
    _out(hold)
    if _service_event(cfg, line):
        _out("event line written to the service log")
    else:
        _out("(service not reachable: nothing was logged)")


def cmd_power_off(args, cfg, data_dir):
    p, info = _plug_checked(cfg)
    w = p.watts() if info["meter"] else None
    _out("about to switch off %s '%s'. It reads %s now%s. The miner stays off until `gbox power off` is followed by `gbox power on`." % (
        info["model"], info["alias"], _fmt_watts(w),
        "" if w is None or w < cfg.power["idle_watts"] else " (that looks like a miner hashing, not a hung one)"))
    _confirm_word("OFF")
    p.off()
    _out("switched off.", _service_hold(cfg, None, "switched off by hand"))
    if _service_event(cfg, "power: switched off by hand (gbox power off; %s before)" % _fmt_watts(w)):
        _out("event line written to the service log")


def cmd_power_on(args, cfg, data_dir):
    p, info = _plug_checked(cfg)
    p.on()
    _out("switched on %s '%s'. The SC-BOX is back hashing in about a minute; allow two or three on other units." % (info["model"], info["alias"]))
    _out(_service_hold(cfg, cfg.power["settle_minutes"], "switched on by hand"))
    if _service_event(cfg, "power: switched on by hand (gbox power on)"):
        _out("event line written to the service log")


def cmd_power(args, cfg, data_dir):
    return {"discover": cmd_power_discover, "init": cmd_power_init, "status": cmd_power_status,
            "cycle": cmd_power_cycle, "off": cmd_power_off, "on": cmd_power_on}[args.power_cmd](args, cfg, data_dir)


def cmd_hold(args, cfg, data_dir):
    if args.minutes == "release":
        status, body = _service_post(cfg, "/api/hold/release", {})
        if status is None:
            _die("service not running (or not at %s): nothing to release" % _service_url(cfg), 2)
        if status != 200:
            _die(body.get("error") if isinstance(body, dict) else "HTTP %s" % status)
        _out("hold released: the watchdog judges again once it has a fresh window of samples")
        return
    minutes = None
    if not args.no_expiry:
        try:
            minutes = int(args.minutes if args.minutes is not None else 60)
        except ValueError:
            _die("minutes must be a whole number, or 'release'")
        if not 1 <= minutes <= 1440:
            _die("minutes must be 1 to 1440 (use --no-expiry for an open-ended hold)")
    status, body = _service_post(cfg, "/api/hold", {"minutes": minutes, "reason": args.reason or ""})
    if status is None:
        _die("service not running (or not at %s): a hold needs `gbox serve` running" % _service_url(cfg), 2)
    if status != 200:
        _die(body.get("error") if isinstance(body, dict) else "HTTP %s" % status)
    h = body["hold"]
    _out("held %s%s: the watchdog judges nothing until the miner hashes twice in a row%s." % (
        ("until " + h["until"]) if h["until"] else "with no expiry", (" (%s)" % h["reason"]) if h["reason"] else "",
        " or the hold expires" if h["until"] else ", or you run `gbox hold release`"))


def cmd_serve(args, cfg, data_dir):
    # pythonw has no stdio; the stock servers write to stderr and would die on it
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if args.bind:
        cfg.bind = args.bind
    if args.port:
        cfg.port = args.port
    if args.interval:
        cfg.poll_interval = args.interval
    if args.host:
        cfg.host = args.host
    if args.board_source:
        cfg.board_source = args.board_source
    cfg.validate()
    if not cfg.host:
        _die("no miner address: run `gbox init` or pass --host")
    if args.remember:
        cfg.password_hex = api.aes.encrypt_password(_password())
        config.save(cfg, data_dir)
        _out("password stored (encrypted form, password-equivalent) in", data_dir / config.CONFIG_NAME)
    if args.forget and cfg.password_hex:
        cfg.password_hex = None
        config.save(cfg, data_dir)
        _out("stored password removed")

    config.ensure_dir(data_dir)
    events = EventLog(data_dir / "events.log")
    note = migrate_columns(data_dir / "log.csv")
    if note:
        events.write("service: log.csv header updated to %d columns (%s)" % (len(COLUMNS), note))
    miner = _miner(args, cfg, need_password=False)
    state = ServiceState(cfg, miner, data_dir, events=events)
    try:
        srv = make_server(state)
    except OSError as e:
        _die("cannot listen on %s:%d (%s). Another gbox serve running?" % (cfg.bind, cfg.port, e))

    plug = None
    if cfg.power is not None and not args.no_power:
        plug = plugmod.make(cfg.power)
        try:
            info = plug.identify()
            events.write("service: power plug %s '%s', meter %s, %s" % (
                info["model"], info["alias"], "yes" if info["meter"] else "no",
                "ARMED: the watchdog may cycle it" if cfg.power["cycle"] else "dry run: the watchdog logs what it would do"))
        except plugmod.PlugError as e:
            events.write("service: power plug did not answer at start (%s); will keep trying each poll" % e)
    wd = None
    if cfg.watchdog.get("enabled", True) and not args.no_watchdog:
        w = cfg.watchdog
        wd = Watchdog(miner.restart, events, cfg.poll_interval, stall_minutes=w["stall_minutes"],
                      unreachable_minutes=w["unreachable_minutes"], min_gap_minutes=w["min_gap_minutes"],
                      max_restarts_per_day=w["max_restarts_per_day"], plug=plug, power=cfg.power)
        wd.seed_from_events(events.tail(4000))     # the caps and a running hold survive this restart
    control = PowerControl(plug, cfg.power, wd, events) if plug is not None else None
    scheduler = None
    if control is not None and cfg.power.get("schedule"):
        scheduler = Scheduler(cfg.power["schedule"], control, events)
        events.write("service: schedule %s" % scheduler.describe())
    poller = Poller(miner, data_dir / "log.csv", cfg.poll_interval, watchdog=wd, events=events, plug=plug, scheduler=scheduler,
                    syslog_interval=cfg.syslog_interval, board_source=cfg.board_source)
    state.poller, state.watchdog, state.power_control = poller, wd, control

    url = "http://%s:%d/" % ("127.0.0.1" if cfg.bind in ("0.0.0.0", "") else cfg.bind, cfg.port)
    # Each service records itself in its OWN data dir, so the live one and a scratch one can be told
    # apart by something other than their identical command lines (2026-09-19).
    pidfile.write(data_dir, cfg.port, __version__)
    events.write("service: started v%s, miner %s, poll %ds, watchdog %s, power plug %s, listening on %s:%d" % (
        __version__, cfg.host, cfg.poll_interval, "on" if wd else "off",
        ("armed" if cfg.power["cycle"] else "dry run") if plug else "none", cfg.bind, cfg.port))
    if cfg.bind not in ("127.0.0.1", "localhost", "::1"):
        _out("WARNING: listening on %s. Anyone who can reach this address can read the miner's status," % cfg.bind)
        _out("         write to the event log and, with the miner password, press the buttons. Use a firewall or a VPN.")
    _out("gbox %s: dashboard at %s  (miner %s, poll every %ds, watchdog %s, power plug %s)" % (
        __version__, url, cfg.host, cfg.poll_interval, "on" if wd else "off",
        ("armed" if cfg.power["cycle"] else "dry run") if plug else "none"))
    if not miner.has_credentials:
        _out("no stored password: the logger starts once you log in on the dashboard")
    _out("data in", data_dir)

    poller.start()
    t = threading.Thread(target=srv.serve_forever, name="gbox-http", daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        srv.shutdown()
        pidfile.remove(data_dir)
        events.write("service: stopped")


# ---------------------------------------------------------------- parser

def build_parser():
    p = argparse.ArgumentParser(prog="gbox", description="Tools for Goldshell Box-series miners.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--host", help="miner address, overrides config.json")
    p.add_argument("--data", help="data directory (default: $GBOX_DATA or ~/.gbox)")
    p.add_argument("--version", action="version", version="gbox " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("discover", help="find Goldshell miners on the local network (no password needed)")
    sp.add_argument("--subnet", help="CIDR to sweep, e.g. 192.168.1.0/24 (default: this machine's own /24)")
    sp.add_argument("--target", action="append", help="probe this address only; repeatable, overrides --subnet")
    sp.add_argument("--timeout", type=float, default=discovermod.DEFAULT_TIMEOUT,
                    help="seconds to wait per address (default: %(default)s)")
    sp.add_argument("--include-configured", action="store_true",
                    help="also probe the miner in config.json; only while `gbox serve` is stopped")
    sp.set_defaults(fn=cmd_discover)
    sp = sub.add_parser("init", help="write config.json")
    sp.add_argument("--interval", type=int, help="poll interval in seconds (min %d)" % config.MIN_POLL_INTERVAL)
    sp.set_defaults(fn=cmd_init)
    sub.add_parser("status", help="current state of the miner").set_defaults(fn=cmd_status)
    sub.add_parser("chips", help="per-chip nonce counts and health").set_defaults(fn=cmd_chips)
    sp = sub.add_parser("plan", help="set the clock via the manual power plan")
    sp.add_argument("mhz", type=int, help="clock in MHz, a multiple of 25")
    sp.add_argument("--volts", type=float, help="core voltage; default keeps the current value")
    sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("fantarget", help="fan controller target temperature on the board sensor")
    sp.add_argument("temp", type=int)
    sp.set_defaults(fn=cmd_fantarget)
    sub.add_parser("restart", help="soft restart the miner").set_defaults(fn=cmd_restart)
    sp = sub.add_parser("trials", help="error and throughput per clock and fan target, from the service log")
    sp.add_argument("--segments", action="store_true", help="one row per continuous run instead of one per clock")
    sp.add_argument("--min", type=int, default=trials.MIN_MINUTES, help="hide runs shorter than this many minutes (default %d)" % trials.MIN_MINUTES)
    sp.set_defaults(fn=cmd_trials)
    tsub = sp.add_subparsers(dest="trials_cmd")
    run = tsub.add_parser("run", help="hold each clock in turn, unattended, with abort rules; needs `gbox serve` running",
                          description="Example: gbox trials run 550 575 600 --hours 4 --end 575")
    run.add_argument("clocks", type=int, nargs="+", help="clocks to hold, in order, MHz (multiples of 25)")
    run.add_argument("--hours", type=float, required=True, help="how long to hold each clock")
    run.add_argument("--end", type=int, help="clock to leave the miner on afterwards, or on abort (default: the lowest in the list)")
    run.add_argument("--fan", type=int, help="fan target to set once at the start")
    run.add_argument("--settle", type=float, default=10, help="minutes to ignore after each clock change, for the fan spike (default 10)")
    run.add_argument("--check", type=float, default=5, help="minutes between checks of the log (default 5)")
    run.add_argument("--judge", type=float, default=30, help="minutes a step must be old before the bad-share rule applies (default 30)")
    run.add_argument("--max-resets", type=int, default=0, help="board resets tolerated in one step before aborting (default 0)")
    run.add_argument("--max-bad", type=float, default=3.0, help="worst chip bad share, percent, tolerated before aborting (default 3)")
    run.set_defaults(fn=cmd_trials)
    sp = sub.add_parser("errors", help="bad share, worst chip, clock and board resets per bucket, from the service log")
    sp.add_argument("--hours", type=int, default=72, help="how far back (default 72)")
    sp.add_argument("--bucket", type=int, default=30, help="bucket width in minutes (default 30)")
    sp.set_defaults(fn=cmd_errors)

    sp = sub.add_parser("power", help="a smart plug that can cut power to a frozen controller (optional)",
                        description="Find, set up, read and, by hand, cycle the smart plug the miner is powered from. "
                                    "The watchdog uses it only after soft restarts have failed, and only once armed.")
    psub = sp.add_subparsers(dest="power_cmd", required=True)
    d = psub.add_parser("discover", help="list plugs that answer on the LAN")
    d.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for answers (default 3)")
    d.add_argument("--port", type=int, default=plugmod.LEGACY_PORT, help=argparse.SUPPRESS)
    d.add_argument("--target", action="append", help=argparse.SUPPRESS)
    i = psub.add_parser("init", help="record the miner's plug in config.json (dry run until armed)")
    i.add_argument("--plug", required=True, help="plug address (IP or hostname, optional :port)")
    i.add_argument("--driver", default="kasa", choices=sorted(plugmod.DRIVERS))
    i.add_argument("--yes", action="store_true", help="skip the 'is this the miner's plug?' question")
    psub.add_parser("status", help="relay, watts, dry run or armed, cycles in 24 h")
    c = psub.add_parser("cycle", help="cut power and restore it, after typing CYCLE")
    c.add_argument("--off-seconds", type=int, help="relay open this long (default: config, %d)" % config.DEFAULT_POWER["off_seconds"])
    psub.add_parser("off", help="switch the plug off, after typing OFF; the miner stays off, unjudged, until `power on`")
    psub.add_parser("on", help="switch the plug on; the watchdog waits the settle gap for the boot")
    sp.set_defaults(fn=cmd_power)

    sp = sub.add_parser("hold", help="tell the running service the miner will be unreachable on purpose",
                        description="A hold stands the watchdog down until the miner hashes twice in a row, the hold "
                                    "expires, or you release it. Use it before pulling the cord, swapping a power supply, "
                                    "or moving the unit. Needs `gbox serve` running.")
    sp.add_argument("minutes", nargs="?", help="how long, 1 to 1440 (default 60); or 'release' to end a hold")
    sp.add_argument("--reason", help="why, for the event log")
    sp.add_argument("--no-expiry", action="store_true", help="hold until the miner is back or you release it")
    sp.set_defaults(fn=cmd_hold)

    sp = sub.add_parser("serve", help="run dashboard, logger and watchdog")
    sp.add_argument("--bind", help="listen address (default 127.0.0.1; anything else is exposed)")
    sp.add_argument("--port", type=int)
    sp.add_argument("--interval", type=int, help="poll interval in seconds (min %d)" % config.MIN_POLL_INTERVAL)
    sp.add_argument("--remember", action="store_true", help="prompt for the password once and store it for unattended restarts")
    sp.add_argument("--forget", action="store_true", help="remove a stored password")
    sp.add_argument("--no-watchdog", action="store_true")
    sp.add_argument("--no-power", action="store_true", help="ignore the power block in config.json for this run")
    sp.add_argument("--board-source", choices=config.BOARD_SOURCES,
                    help="per-board data transport for a multi-board unit: auto (default), 4028 or minerinfo")
    sp.set_defaults(fn=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_dir = config.ensure_dir(args.data) if args.data else config.default_data_dir()
    cfg = config.load(data_dir)
    try:
        args.fn(args, cfg, data_dir)
    except api.NoCredentials:
        _die("no credentials")
    except api.AuthError as e:
        _die(e)
    except api.MinerError as e:
        _die("miner: %s" % e)
    except plugmod.PlugError as e:
        _die("plug: %s" % e, 2)
    except ValueError as e:
        _die(e)


if __name__ == "__main__":
    main()
