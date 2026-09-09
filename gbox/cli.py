"""The gbox command line.

    gbox init                      write config.json (miner address, poll interval)
    gbox status                    hashrate, errors, fans, temperatures, reset counter
    gbox chips                     per-chip good/bad nonce table with weak/failing flags
    gbox plan 600                  set the clock (25 MHz steps) via the manual power plan
    gbox fantarget 65              fan controller target on the board sensor
    gbox restart                   soft restart
    gbox trials                    error and throughput per clock, from the service log
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

from . import __version__, api, config, trials
from .events import EventLog
from .poller import COLUMNS, Poller, migrate_columns
from .server import ServiceState, make_server
from .watchdog import Watchdog


def _out(*a):
    print(*a)


def _die(msg, code=1):
    print("gbox: " + str(msg), file=sys.stderr)
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
    if migrate_columns(data_dir / "log.csv"):
        events.write("service: log.csv header updated to %d columns (copy kept as log.csv.bak)" % len(COLUMNS))
    miner = _miner(args, cfg, need_password=False)
    state = ServiceState(cfg, miner, data_dir, events=events)
    try:
        srv = make_server(state)
    except OSError as e:
        _die("cannot listen on %s:%d (%s). Another gbox serve running?" % (cfg.bind, cfg.port, e))

    wd = None
    if cfg.watchdog.get("enabled", True) and not args.no_watchdog:
        w = cfg.watchdog
        wd = Watchdog(miner.restart, events, cfg.poll_interval, stall_minutes=w["stall_minutes"],
                      unreachable_minutes=w["unreachable_minutes"], min_gap_minutes=w["min_gap_minutes"],
                      max_restarts_per_day=w["max_restarts_per_day"])
    poller = Poller(miner, data_dir / "log.csv", cfg.poll_interval, watchdog=wd, events=events)
    state.poller, state.watchdog = poller, wd

    url = "http://%s:%d/" % ("127.0.0.1" if cfg.bind in ("0.0.0.0", "") else cfg.bind, cfg.port)
    events.write("service: started v%s, miner %s, poll %ds, watchdog %s, listening on %s:%d" % (
        __version__, cfg.host, cfg.poll_interval, "on" if wd else "off", cfg.bind, cfg.port))
    if cfg.bind not in ("127.0.0.1", "localhost", "::1"):
        _out("WARNING: listening on %s. Anyone who can reach this address can read the miner's status," % cfg.bind)
        _out("         write to the event log and, with the miner password, press the buttons. Use a firewall or a VPN.")
    _out("gbox %s: dashboard at %s  (miner %s, poll every %ds, watchdog %s)" % (
        __version__, url, cfg.host, cfg.poll_interval, "on" if wd else "off"))
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
        events.write("service: stopped")


# ---------------------------------------------------------------- parser

def build_parser():
    p = argparse.ArgumentParser(prog="gbox", description="Tools for Goldshell Box-series miners.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--host", help="miner address, overrides config.json")
    p.add_argument("--data", help="data directory (default: $GBOX_DATA or ~/.gbox)")
    p.add_argument("--version", action="version", version="gbox " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

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
    sp = sub.add_parser("serve", help="run dashboard, logger and watchdog")
    sp.add_argument("--bind", help="listen address (default 127.0.0.1; anything else is exposed)")
    sp.add_argument("--port", type=int)
    sp.add_argument("--interval", type=int, help="poll interval in seconds (min %d)" % config.MIN_POLL_INTERVAL)
    sp.add_argument("--remember", action="store_true", help="prompt for the password once and store it for unattended restarts")
    sp.add_argument("--forget", action="store_true", help="remove a stored password")
    sp.add_argument("--no-watchdog", action="store_true")
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
    except ValueError as e:
        _die(e)


if __name__ == "__main__":
    main()
