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


class Config:
    def __init__(self, host="", poll_interval=30, bind="127.0.0.1", port=8765,
                 password_hex=None, watchdog=None):
        self.host = host
        self.poll_interval = int(poll_interval)
        self.bind = bind
        self.port = int(port)
        self.password_hex = password_hex
        self.watchdog = dict(DEFAULT_WATCHDOG)
        self.watchdog.update(watchdog or {})

    def validate(self):
        if self.poll_interval < MIN_POLL_INTERVAL:
            raise ValueError("poll_interval must be at least %d seconds" % MIN_POLL_INTERVAL)
        if not 1 <= self.port <= 65535:
            raise ValueError("port out of range")
        return self

    def to_dict(self, include_secret=True):
        d = {"host": self.host, "poll_interval": self.poll_interval, "bind": self.bind,
             "port": self.port, "watchdog": self.watchdog}
        if include_secret and self.password_hex:
            d["password_hex"] = self.password_hex
        return d

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in ("host", "poll_interval", "bind", "port", "password_hex", "watchdog") if k in d}
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
