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

DEFAULT_WATCHDOG = {
    "enabled": True,
    "stall_minutes": 5,          # accepted-share counter frozen this long -> restart
    "unreachable_minutes": 2,    # HTTP failing this long -> restart
    "min_gap_minutes": 10,       # settle time after a restart before judging again
    "max_restarts_per_day": 6,
}

# The optional smart-plug block. Absent: no plug, nothing changes. Present: the
# watchdog may cut power to a frozen controller after soft restarts have failed.
# `cycle` false is a dry run: the event log says what would have happened.
DEFAULT_POWER = {
    "driver": "kasa",
    "host": "",                  # plug address; `gbox power discover` finds it
    "device_id": "",             # recorded by `gbox power init`; no cycle unless the plug matches
    "cycle": False,              # true arms the watchdog; false logs "would cycle" only
    "after_minutes": 15,         # unreachable this long, with two failed soft restarts, before a cycle
    "off_seconds": 15,           # relay open this long
    "settle_minutes": 20,        # nothing judged this long after a cycle
    "max_cycles_per_day": 3,
    "idle_watts": 100,           # below this the miner is idle (hung draws about 34 W, hashing 180+)
}
PLUG_DRIVERS = ("kasa",)


class Config:
    def __init__(self, host="", poll_interval=30, bind="127.0.0.1", port=8765,
                 password_hex=None, watchdog=None, power=None):
        self.host = host
        self.poll_interval = int(poll_interval)
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
        return self

    def to_dict(self, include_secret=True):
        d = {"host": self.host, "poll_interval": self.poll_interval, "bind": self.bind,
             "port": self.port, "watchdog": self.watchdog}
        if self.power is not None:
            d["power"] = self.power
        if include_secret and self.password_hex:
            d["password_hex"] = self.password_hex
        return d

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in ("host", "poll_interval", "bind", "port", "password_hex", "watchdog", "power") if k in d}
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
