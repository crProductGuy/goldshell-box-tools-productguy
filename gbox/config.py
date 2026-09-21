"""Configuration and the data directory.

Everything the tools write lives in one directory: config.json, log.csv,
events.log. The default is ~/.gbox, overridable with the GBOX_DATA
environment variable or --data on the command line. Nothing is written
inside the package or the repository.

config.json holds no secret unless `gbox serve --remember` was used, in which
case it holds the password in the firmware's own encrypted form. That form is
password-equivalent (see docs/firmware-api.md), so the file is created with
owner-only permissions where the platform supports them.
"""
import json
import os
import stat
from pathlib import Path

CONFIG_NAME = "config.json"
MIN_POLL_INTERVAL = 10          # seconds; faster polling crashes the miner's web backend
MIN_SYSLOG_INTERVAL = 60        # seconds between cgminer-log reads (about 2 MB each); 0 turns the read off

DEFAULT_WATCHDOG = {
    "enabled": True,
    "stall_minutes": 5,          # accepted-share counter frozen this long -> restart
    "unreachable_minutes": 2,    # HTTP failing this long -> restart
    "absent_minutes": 2,         # 0.9.0: answering, but clock 0 and no board sensor, this long -> restart; 0 is off
    "min_gap_minutes": 5,        # settle time after a restart before judging again (10 until 2026-09-12; a soft restart takes 60-90 s)
    "max_restarts_per_day": 12,  # at least twice power.max_cycles_per_day plus a few: a cycle needs two failed attempts
}

# The optional smart-plug block. Absent: no plug, nothing changes. Present: the
# watchdog may cut power to a frozen controller after soft restarts have failed.
# `cycle` false is a dry run: the event log says what would have happened.
DEFAULT_POWER = {
    "driver": "kasa",
    "host": "",                  # plug address; `gbox power discover` finds it
    "device_id": "",             # recorded by `gbox power init`; no cycle unless the plug matches
    "cycle": False,              # true arms the watchdog; false logs "would cycle" only
    "after_minutes": 5,          # unreachable this long, with two failed soft restarts, before a cycle (15 until 2026-09-12: 17 min of lost hashing per freeze)
    "off_seconds": 15,           # relay open this long
    "settle_minutes": 6,         # nothing judged this long after POWER RETURNS, and it ends early once the miner hashes twice (20 until 2026-09-17: across 25 measured boots the miner was hashing at 5-25 s, so 20 only hid a dead hashboard for 22 min)
    "max_cycles_per_day": 3,
    "idle_watts": 100,           # below this the miner is idle (hung draws about 34 W, hashing 180+)
    "boot_watts": 20,            # 0.7.2: under this AND silent on the network, the controller never came up. Only ever refines the remedy; the fault itself is decided without a meter (2026-09-19)
    "boot_check_minutes": 2,     # first reading this long after power returns, verdict at twice it (2026-09-15 06:40: 12 W for 25 min after a cycle); one repeat cycle. 0: no check
}
PLUG_DRIVERS = ("kasa",)

# 0.7.0: the hottest chip, read from the cgminer log every syslog_interval seconds (0 turns the read off).
# The flags sit on the sustained level (the 5-minute median of the 5-second readings), not on the peak:
# on the unit this was built against the peak reads 90+ a few times an hour at normal operation while the
# level sits at 81 to 82. Thresholds from that unit's record (steady p50 81, p95 86); not sourced beyond it.
DEFAULT_SYSLOG_INTERVAL = 300
DEFAULT_TEMPS = {
    "hot_serious": 85,           # sustained level at or above this: the tile and the header badge turn serious
    "hot_critical": 90,          # and critical
}
# 0.8.0: the data directory stops growing without limit. At the cap, log.csv and boards.csv carry the last
# keep_hours into a fresh file and the whole old one becomes .1 (gbox.poller.rotate); events.log carries its
# last lines the same way. 25 MB is about six weeks of log.csv at a 30 s poll. max_mb 0 turns all of it off.
DEFAULT_LOG = {
    "max_mb": 25,                # 0 (off), or 5 to 1000
    "keep_hours": 72,            # 24 to 168; the three-day errors chart asks for exactly 72
}
MIN_LOG_MB, MAX_LOG_MB = 5, 1000
MIN_KEEP_HOURS, MAX_KEEP_HOURS = 24, 168

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# 0.8.0: which transport a multi-board unit's per-board data comes from. "auto" probes port 4028 once
# and falls back to /dbg/minerinfo on failure (gbox.poller.Poller); "4028" and "minerinfo" force one.
BOARD_SOURCES = ("auto", "4028", "minerinfo")


def parse_hhmm(text):
    """"23:05" -> (23, 5); ValueError for anything else."""
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":" or not (text[:2] + text[3:]).isdigit():
        raise ValueError("not HH:MM")
    h, m = int(text[:2]), int(text[3:])
    if h > 23 or m > 59:
        raise ValueError("not HH:MM")
    return h, m


def validate_schedule(sched):
    """The optional power.schedule block: {"off": "23:00", "on": "06:00", "days": [...]}. Raises ValueError."""
    if not isinstance(sched, dict):
        raise ValueError("power.schedule must be an object with \"off\" and \"on\" times")
    times = {}
    for key in ("off", "on"):
        try:
            times[key] = parse_hhmm(sched.get(key))
        except ValueError:
            raise ValueError("power.schedule.%s must be a time like \"23:00\"" % key) from None
    if times["off"] == times["on"]:
        raise ValueError("power.schedule: off and on must be different times")
    days = sched.get("days")
    if days is not None and (not isinstance(days, list) or not days or any(d not in DAYS for d in days)):
        raise ValueError("power.schedule.days must be a non-empty list from: %s" % ", ".join(DAYS))


class Config:
    def __init__(self, host="", poll_interval=30, bind="127.0.0.1", port=8765,
                 password_hex=None, watchdog=None, power=None, syslog_interval=DEFAULT_SYSLOG_INTERVAL, temps=None,
                 board_source="auto", log=None):
        self.host = host
        self.poll_interval = int(poll_interval)
        self.syslog_interval = int(syslog_interval)
        self.board_source = board_source
        self.temps = dict(DEFAULT_TEMPS)
        self.temps.update(temps or {})
        self.log = dict(DEFAULT_LOG)
        self.log.update(log or {})
        self.bind = bind
        self.port = int(port)
        self.password_hex = password_hex
        self.watchdog = dict(DEFAULT_WATCHDOG)
        self.watchdog.update(watchdog or {})
        self.power = power

    @property
    def power(self):
        """The smart-plug block with defaults filled in, or None when no plug is configured."""
        return self._power

    @power.setter
    def power(self, block):
        if block is None:
            self._power = None
        else:
            self._power = dict(DEFAULT_POWER)
            self._power.update(block)

    def validate(self):
        if self.poll_interval < MIN_POLL_INTERVAL:
            raise ValueError("poll_interval must be at least %d seconds" % MIN_POLL_INTERVAL)
        if not 1 <= self.port <= 65535:
            raise ValueError("port out of range")
        if self.syslog_interval != 0 and self.syslog_interval < MIN_SYSLOG_INTERVAL:
            raise ValueError("syslog_interval must be 0 (off) or at least %d seconds" % MIN_SYSLOG_INTERVAL)
        absent = self.watchdog.get("absent_minutes")
        if isinstance(absent, bool) or not isinstance(absent, (int, float)) or absent < 0 or 0 < absent < 1:
            raise ValueError("watchdog.absent_minutes must be 0 (off) or at least 1")
        if self.board_source not in BOARD_SOURCES:
            raise ValueError("board_source must be one of: %s" % ", ".join(BOARD_SOURCES))
        serious, critical = int(self.temps["hot_serious"]), int(self.temps["hot_critical"])
        if not 40 <= serious < critical <= 120:
            raise ValueError("temps.hot_serious must be below temps.hot_critical, both between 40 and 120")
        max_mb, keep_hours = int(self.log["max_mb"]), int(self.log["keep_hours"])
        if max_mb != 0 and not MIN_LOG_MB <= max_mb <= MAX_LOG_MB:
            raise ValueError("log.max_mb must be 0 (no rotation) or %d to %d" % (MIN_LOG_MB, MAX_LOG_MB))
        if not MIN_KEEP_HOURS <= keep_hours <= MAX_KEEP_HOURS:
            raise ValueError("log.keep_hours must be %d to %d" % (MIN_KEEP_HOURS, MAX_KEEP_HOURS))
        if self.power is not None:
            p = self.power
            if p.get("driver") not in PLUG_DRIVERS:
                raise ValueError("power.driver must be one of: %s" % ", ".join(PLUG_DRIVERS))
            if not p.get("host"):
                raise ValueError("power.host is empty: run `gbox power init --host <plug>`")
            if not 3 <= int(p["off_seconds"]) <= 120:
                raise ValueError("power.off_seconds must be 3 to 120")
            if int(p["after_minutes"]) < int(self.watchdog["unreachable_minutes"]):
                raise ValueError("power.after_minutes must be at least watchdog.unreachable_minutes")
            if not 0 <= int(p["max_cycles_per_day"]) <= 10:
                raise ValueError("power.max_cycles_per_day must be 0 to 10")
            # Both floors are flat, and both are measured from the moment power comes back (2026-09-19).
            if not 2 <= float(p["settle_minutes"]) <= 60:
                raise ValueError("power.settle_minutes must be 2 to 60: below 2 the watchdog judges a miner that "
                                 "is still booting (60 to 66 s measured), and a large value hides a dead "
                                 "hashboard for exactly that long")
            # 1 is rejected on purpose: every one of the five known cold-start failures drew over boot_watts
            # transiently in its first 30 to 70 s before collapsing, so a check that early reads "booted fine".
            if int(p["boot_check_minutes"]) != 0 and not 2 <= int(p["boot_check_minutes"]) <= 10:
                raise ValueError("power.boot_check_minutes must be 0 (no check) or 2 to 10: a check before two "
                                 "minutes lands in the transient draw of a boot that is actually failing")
            if not 0 <= int(p["boot_watts"]) < int(p["idle_watts"]):
                raise ValueError("power.boot_watts must be below power.idle_watts")
            if int(self.watchdog["max_restarts_per_day"]) < 2 * int(p["max_cycles_per_day"]):
                raise ValueError("watchdog.max_restarts_per_day must be at least twice power.max_cycles_per_day: "
                                 "a cycle needs two failed soft restarts, and every attempt uses a restart slot")
            if p.get("schedule") is not None:
                validate_schedule(p["schedule"])
        return self

    def to_dict(self, include_secret=True):
        d = {"host": self.host, "poll_interval": self.poll_interval, "bind": self.bind,
             "port": self.port, "watchdog": self.watchdog, "syslog_interval": self.syslog_interval, "temps": self.temps,
             "log": self.log, "board_source": self.board_source}
        if self.power is not None:
            d["power"] = self.power
        if include_secret and self.password_hex:
            d["password_hex"] = self.password_hex
        return d

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in ("host", "poll_interval", "bind", "port", "password_hex", "watchdog", "power",
                                   "syslog_interval", "temps", "board_source", "log") if k in d}
        return cls(**known)


def default_data_dir():
    env = os.environ.get("GBOX_DATA")
    return Path(env).expanduser() if env else Path.home() / ".gbox"


def ensure_dir(data_dir):
    p = Path(data_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load(data_dir):
    """The config in data_dir, or defaults when there is none."""
    path = Path(data_dir) / CONFIG_NAME
    if not path.exists():
        return Config()
    with open(path, encoding="utf-8") as f:
        return Config.from_dict(json.load(f))


def save(cfg, data_dir):
    """Write config.json, owner-readable only where the OS supports it. Returns the path."""
    ensure_dir(data_dir)
    path = Path(data_dir) / CONFIG_NAME
    body = json.dumps(cfg.to_dict(), indent=2) + "\n"
    if cfg.password_hex and os.name == "posix":
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    return path
