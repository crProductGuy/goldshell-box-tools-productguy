"""Read-only probe for one HS-BOX: a fixed list of 21 requests, ten seconds apart, redacted captures.

    python -m tools.hs_probe --label hs1 --host <address> --dry-run
    GBOX_STUDY_PASSWORD=... python -m tools.hs_probe --label hs1 --host <address>

What it is for: the HS-BOX study (see the evolution log). One run per unit, started by the owner.
It is not part of the `gbox` package and nothing in `gbox` imports it.

Safety, all of it enforced here and proven against `tests/fake_miner.py`:

- The list of requests is `ALLOWLIST`. The sender refuses any HTTP method except GET and any path or
  port-4028 command that is not in that tuple, before a socket opens. There is no way to ask it for a
  write, and no second login: a 401 is recorded and the run goes on.
- Strictly one request at a time, at least `--interval` seconds (never under 10) from one start to the
  next. 15 s timeout. Three connection failures in a row abort the run.
- Guards, checked before any contact (exit 2): the target must not be the host in the live gbox config
  (the service watches that miner), and the output directory must not be inside a git work tree.
- Redaction happens before a byte is written: pool user, pool password, worker names, SSID and WiFi
  key learned from the pools and wifisetting reads are replaced everywhere, MAC addresses and the token
  are never written. If no pool user was learned, the miner log (which repeats the wallet) is withheld.
- The password comes from the environment or a terminal prompt, never from an argument. No token,
  password, query string or URL goes to a file, stdout or stderr.
"""
import argparse
import getpass
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from gbox import aes, config

MIN_INTERVAL = 10               # seconds start to start; the firmware's web backend crashes under bursts
TIMEOUT = 15                    # seconds per request
MAX_CONNECTION_FAILURES = 3     # in a row, then the run aborts
MAX_BODY = 8 * 1024 * 1024
PASSWORD_ENV = "GBOX_STUDY_PASSWORD"
LOGIN_PATH = "/user/login"

# Where a request gets its credentials from.
NONE, LOGIN, TOKEN = "none", "login", "token"
# Which reads teach the redactor what to hide.
POOLS_4028, POOLS_HTTP, WIFI = "pools4028", "pools", "wifi"

# (name, transport, path or command, credentials, secret source). Table order is run order.
ALLOWLIST = (
    ("4028-version", "4028", "version", NONE, None),
    ("4028-summary", "4028", "summary", NONE, None),
    ("4028-devs", "4028", "devs", NONE, None),
    ("4028-stats", "4028", "stats", NONE, None),
    ("4028-pools", "4028", "pools", NONE, POOLS_4028),
    ("status-notoken", "http", "/mcb/status", NONE, None),
    ("login", "http", LOGIN_PATH, LOGIN, None),
    ("status", "http", "/mcb/status", TOKEN, None),
    ("setting", "http", "/mcb/setting", TOKEN, None),
    ("pools", "http", "/mcb/pools", TOKEN, POOLS_HTTP),
    ("wifisetting", "http", "/mcb/wifisetting", TOKEN, WIFI),
    ("cgminer", "http", "/mcb/cgminer", TOKEN, None),
    ("hshistory", "http", "/cpb/hshistory", TOKEN, None),
    ("minerinfo", "http", "/dbg/minerinfo", TOKEN, None),
    ("icinfo", "http", "/dbg/icinfo", TOKEN, None),
    ("fanctrllog", "http", "/dbg/fanctrllog", TOKEN, None),
    ("syslog", "http", "/dbg/syslog", TOKEN, None),
    ("minersyslog", "http", "/dbg/minersyslog", TOKEN, None),
    ("vsinfo", "http", "/dbg/vsinfo", TOKEN, None),
    ("protect", "http", "/mcb/protect", TOKEN, None),
    ("defend", "http", "/mcb/defend", TOKEN, None),
)
NAMES = tuple(r[0] for r in ALLOWLIST)
MINERSYSLOG = "minersyslog"
_HTTP_PATHS = frozenset(r[2] for r in ALLOWLIST if r[1] == "http")
_COMMANDS = frozenset(r[2] for r in ALLOWLIST if r[1] == "4028")


class NotAllowed(Exception):
    """The request is not on the allowlist. Raised before any socket is opened."""


class Unreachable(Exception):
    """A connection failure (refused, timed out, dropped). The message is the exception class only."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Sender:
    """The only code that opens a socket. One request per call, no retries, no logging of targets."""

    def __init__(self, host, port4028=4028, timeout=TIMEOUT):
        self.host = _strip_scheme(host)
        self.hostname = _hostname(host)
        self.port4028 = port4028
        self.timeout = timeout
        # no proxy from the environment, and a redirect is an answer, not something to follow
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def http(self, method, path, token=None, query=None):
        """GET one allowlisted path. Returns (status, body bytes). Any 4xx/5xx is an answer, not an error."""
        if method != "GET":
            raise NotAllowed("only GET is allowed")
        if path not in _HTTP_PATHS:
            raise NotAllowed("path is not on the allowlist")
        if query is not None and path != LOGIN_PATH:
            raise NotAllowed("only the login takes a query")
        url = "http://%s%s" % (self.host, path)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return r.status, r.read(MAX_BODY)
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read(MAX_BODY)
            except (OSError, http.client.HTTPException):
                return e.code, b""
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise Unreachable(type(e).__name__) from None

    def raw4028(self, command):
        """Send one allowlisted command to port 4028, read until the miner closes. Returns bytes."""
        if command not in _COMMANDS:
            raise NotAllowed("command is not on the allowlist")
        try:
            s = socket.create_connection((self.hostname, self.port4028), self.timeout)
        except OSError as e:
            raise Unreachable(type(e).__name__) from None
        buf = b""
        try:
            s.settimeout(self.timeout)
            s.sendall(json.dumps({"command": command}).encode("utf-8"))
            while len(buf) < MAX_BODY:
                d = s.recv(65536)
                if not d:
                    break
                buf += d
        except OSError as e:
            if not buf:
                raise Unreachable(type(e).__name__) from None
        finally:
            s.close()
        return buf


# ---------------------------------------------------------------- redaction

_MAC = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f])")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_JWT_FIELD = re.compile(r'("JWT Token"\s*:\s*)"[^"]*"')
# a JSON "key": value pair whose value is a string or a bare number
_FIELD = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:\s*)(?:"((?:[^"\\]|\\.)*)"|(-?\d[\d.eE+-]*))')

POOL_USER, POOL_PASS, SSID, WIFI_KEY = "pool-user", "pool-pass", "ssid", "wifi-key"
MIN_SECRET = 4


def _placeholder(kind):
    return "<redacted:%s>" % kind


def _classify(source, key):
    k = key.lower()
    if source in (POOLS_4028, POOLS_HTTP):
        if "pass" in k or "pwd" in k:
            return POOL_PASS
        if "user" in k or "worker" in k or "account" in k:
            return POOL_USER
    elif source == WIFI:
        if "ssid" in k:
            return SSID
        if any(t in k for t in ("key", "pass", "psk", "pwd", "secret")):
            return WIFI_KEY
    return None


def _unescape(raw):
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


class Redactor:
    def __init__(self):
        self.fixed = {}             # secret text -> placeholder; only strings of MIN_SECRET characters or more
        self.pool_users = 0         # pool user values seen, of any length
        self.long_pool_users = 0    # ... of MIN_SECRET characters or more, which the text replacement can catch
        self._rx = None

    def add_literal(self, text, kind):
        if text and len(text) >= MIN_SECRET:
            self.fixed[text] = _placeholder(kind)
            self._rx = None

    def learn(self, source, text):
        """Collect the secret strings a pools or wifisetting read shows."""
        for m in _FIELD.finditer(text):
            kind = _classify(source, m.group(1))
            if kind is None:
                continue
            raw = m.group(3) if m.group(3) is not None else m.group(4)
            value = _unescape(raw) if m.group(3) is not None else raw
            if not value:
                continue
            if kind == POOL_USER:
                self.pool_users += 1
                if len(value) >= MIN_SECRET:
                    self.long_pool_users += 1
            self.add_literal(value, kind)
            self.add_literal(raw, kind)

    @property
    def has_pool_user(self):
        return self.long_pool_users > 0 and self.pool_users > 0

    def _scrub_fields(self, source, text):
        count = [0]

        def sub(m):
            kind = _classify(source, m.group(1))
            if kind is None:
                return m.group(0)
            raw = m.group(3) if m.group(3) is not None else m.group(4)
            if not raw:
                return m.group(0)
            count[0] += 1
            return '"%s"%s"%s"' % (m.group(1), m.group(2), _placeholder(kind))

        return _FIELD.sub(sub, text), count[0]

    def apply(self, text, source=None):
        """Redacted text and the number of replacements. Called on every capture before it is written."""
        n = 0
        if source:
            text, c = self._scrub_fields(source, text)
            n += c
        text, c = _JWT_FIELD.subn(lambda m: m.group(1) + '"' + _placeholder("token") + '"', text)
        n += c
        text, c = _BEARER.subn(_placeholder("token"), text)
        n += c
        text, c = _JWT.subn(_placeholder("token"), text)
        n += c
        if self.fixed:
            if self._rx is None:
                self._rx = re.compile("|".join(re.escape(s) for s in sorted(self.fixed, key=len, reverse=True)))
            text, c = self._rx.subn(lambda m: self.fixed[m.group(0)], text)
            n += c
        text, c = _MAC.subn(_placeholder("mac"), text)
        n += c
        return text, n


# ---------------------------------------------------------------- the run

class Result:
    def __init__(self, n, row):
        self.n, (self.name, self.transport, self.target, self.creds, self.source) = n, row
        self.started = None
        self.elapsed_ms = None
        self.status = "skipped"
        self.raw = None             # bytes, held in memory until the end of the run
        self.size = 0
        self.redactions = 0
        self.saved = "skipped"
        self.note = None


def plan(only=None, have_password=True):
    """The (number, row) pairs a run would make, in table order. Login is added when a selected read needs a token."""
    chosen = set(only or NAMES)
    if have_password and any(r[3] == TOKEN and r[0] in chosen for r in ALLOWLIST):
        chosen.add("login")
    return [(i, r) for i, r in enumerate(ALLOWLIST, 1) if r[0] in chosen]


class Probe:
    def __init__(self, sender, password=None, interval=MIN_INTERVAL, only=None,
                 clock=time.monotonic, sleep=time.sleep, emit=None):
        if interval < MIN_INTERVAL:
            raise ValueError("interval under %d s" % MIN_INTERVAL)
        self.sender, self.interval, self.clock, self.sleep = sender, interval, clock, sleep
        self.password_hex = aes.encrypt_password(password) if password else None
        self.only = only
        self.emit = emit or (lambda r: None)
        self.redactor = Redactor()
        if self.password_hex:
            self.redactor.add_literal(self.password_hex, "password-hex")
        self.aborted = False
        self.results = []

    def _wait(self, last_start):
        while last_start is not None:
            gap = self.interval - (self.clock() - last_start)
            if gap <= 0:
                return
            self.sleep(gap)

    def run(self):
        results = self.results = []
        token = None
        last_start = None
        failures = 0
        for n, row in plan(self.only, bool(self.password_hex)):
            r = Result(n, row)
            results.append(r)
            if self.aborted:
                r.status, r.saved, r.note = "aborted", "aborted", "run aborted after repeated connection failures"
                self.emit(r)
                continue
            if r.creds != NONE and not self.password_hex:
                r.note = "no credentials"
                self.emit(r)
                continue
            if r.creds == TOKEN and not token:
                r.note = "login failed"
                self.emit(r)
                continue
            self._wait(last_start)
            last_start = self.clock()
            r.started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            t0 = time.monotonic()
            try:
                self._request(r, token)
                failures = 0
                if r.creds == LOGIN:
                    token = self._token_from(r)
            except Unreachable as e:
                r.status, r.note = "error", str(e)
                failures += 1
                if failures >= MAX_CONNECTION_FAILURES:
                    self.aborted = True
            r.elapsed_ms = int((time.monotonic() - t0) * 1000)
            if r.raw is not None and r.source:
                self.redactor.learn(r.source, _text(r.raw))
            if token:
                self.redactor.add_literal(token, "token")
            self.emit(r)
        return results

    def _request(self, r, token):
        if r.transport == "4028":
            r.raw = self.sender.raw4028(r.target)
            r.status = "ok"
        elif r.creds == LOGIN:
            q = {"username": "admin", "password": self.password_hex, "cipher": "true"}
            r.status, r.raw = self.sender.http("GET", r.target, None, q)
        else:
            r.status, r.raw = self.sender.http("GET", r.target, token if r.creds == TOKEN else None)
        r.size = len(r.raw)

    @staticmethod
    def _token_from(r):
        """The login reply is read for its token and then dropped: it is never saved."""
        raw, r.raw = r.raw, None
        try:
            tok = json.loads(_text(raw)).get("JWT Token")
        except (ValueError, AttributeError):
            tok = None
        if r.status == 200 and isinstance(tok, str) and tok.count(".") == 2:
            r.status = "ok"
            return tok
        r.status = "fail"
        return None


def _text(raw):
    return raw.decode("utf-8", "replace").replace(chr(0), "")


def _is_json(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def write_captures(results, redactor, run_dir):
    """Redact every capture with everything learned in the run, write the files and the manifest."""
    entries = []
    for r in results:
        if r.raw is not None:
            if r.name == MINERSYSLOG and not redactor.has_pool_user:
                r.saved = "withheld"
                r.note = "no pool user was learned from either pools read, so the wallet cannot be redacted"
            else:
                text, r.redactions = redactor.apply(_text(r.raw), r.source)
                fname = "%02d-%s.%s" % (r.n, r.name, "json" if _is_json(text) else "txt")
                with open(os.path.join(run_dir, fname), "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                r.saved = fname
        elif r.creds == LOGIN and r.started:
            r.saved, r.note = "withheld", "the login reply is never saved"
        entries.append({
            "n": r.n, "name": r.name, "transport": r.transport, "target": r.target,
            "started_utc": r.started, "elapsed_ms": r.elapsed_ms, "status": r.status,
            "bytes": r.size, "redactions": r.redactions, "saved": r.saved, "note": r.note,
        })
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"requests": entries}, f, indent=2)
        f.write("\n")
    return entries


# ---------------------------------------------------------------- command line

def _strip_scheme(host):
    h = host.strip()
    if "://" in h:
        h = h.split("://", 1)[1]
    return h.split("/", 1)[0]


def _hostname(host):
    try:
        return (urllib.parse.urlsplit("//" + _strip_scheme(host)).hostname or "").lower()
    except ValueError:
        return ""


def _interval(text):
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number")
    if v < MIN_INTERVAL:
        raise argparse.ArgumentTypeError("must be at least %d seconds" % MIN_INTERVAL)
    return v


def _label(text):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", text) or text in (".", ".."):
        raise argparse.ArgumentTypeError("use letters, digits, dot, dash, underscore (1 to 32)")
    return text


def build_parser():
    p = argparse.ArgumentParser(prog="hs_probe", description="Read-only HS-BOX probe: 21 fixed requests, "
                                "at least 10 s apart, redacted captures outside the repo.")
    p.add_argument("--label", required=True, type=_label, help="name for this unit, for example hs1")
    p.add_argument("--host", required=True, help="the unit's address, optionally host:port for HTTP")
    p.add_argument("--port4028", type=int, default=4028, help=argparse.SUPPRESS)
    p.add_argument("--out", help="output directory (default: <gbox data dir>/study)")
    p.add_argument("--interval", type=_interval, default=float(MIN_INTERVAL),
                   help="seconds from one request start to the next, at least 10 (default 10)")
    p.add_argument("--only", help="comma-separated request names, run in table order: " + ",".join(NAMES))
    p.add_argument("--dry-run", action="store_true", help="print the plan and the output path, contact nothing")
    p.add_argument("--allow-watched-host", action="store_true",
                   help="allow the host in the live gbox config (the service watches that miner)")
    return p


def data_dir(environ):
    env = environ.get("GBOX_DATA")
    return Path(env).expanduser() if env else Path.home() / ".gbox"


def inside_git_worktree(path):
    p = Path(os.path.abspath(path))
    return any((q / ".git").exists() for q in (p, *p.parents))


def main(argv=None, environ=None, isatty=None, clock=time.monotonic, sleep=time.sleep,
         stdout=None, stderr=None):
    environ = os.environ if environ is None else environ
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]
        bad = [s for s in only if s not in NAMES]
        if bad:
            print("hs_probe: unknown request name(s): %s" % ", ".join(bad), file=err)
            return 2

    # guards: nothing has been opened yet
    ddir = data_dir(environ)
    try:
        watched = _hostname(config.load(ddir).host or "")
    except Exception:
        print("hs_probe: refused: cannot read the gbox config, so cannot tell whether this host is watched", file=err)
        return 2
    if watched and watched == _hostname(args.host) and not args.allow_watched_host:
        print("hs_probe: refused: this host is the miner in the live gbox config (the service watches it); "
              "use --allow-watched-host only with the owner's word", file=err)
        return 2
    base = Path(args.out).expanduser() if args.out else ddir / "study"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / args.label / stamp
    if inside_git_worktree(run_dir):
        print("hs_probe: refused: the output directory is inside a git work tree; use --out elsewhere", file=err)
        return 2

    if args.dry_run:
        for n, row in plan(only, True):
            tail = "" if row[3] == NONE else "   [needs login]"
            print("%02d  %-15s %-5s %s%s" % (n, row[0], row[1], row[2], tail), file=out)
        print("output: %s" % run_dir, file=out)
        print("dry run: no connection made", file=out)
        return 0

    password = environ.get(PASSWORD_ENV) or None
    tty = isatty() if isatty else sys.stdin.isatty()
    if password is None and tty:
        password = getpass.getpass("Miner password (leave empty to run without one): ") or None

    os.makedirs(run_dir)

    def emit(r):
        print("%02d  %-15s %-8s %8s B  %s" % (r.n, r.name, r.status, r.size, r.note or ""), file=out)
        out.flush()

    probe = Probe(Sender(args.host, args.port4028), password, args.interval, only, clock, sleep, emit)
    try:
        probe.run()
    finally:
        # an aborted or interrupted run still leaves what it has: anything not reached is marked aborted
        results = probe.results
        done = {r.n for r in results}
        for n, row in plan(only, bool(password)):
            if n not in done:
                r = Result(n, row)
                r.status, r.saved, r.note = "aborted", "aborted", "run interrupted"
                results.append(r)
        entries = write_captures(results, probe.redactor, run_dir)
    saved = sum(1 for e in entries if e["saved"].endswith((".json", ".txt")))
    print("saved %d captures and manifest.json to %s" % (saved, run_dir), file=out)
    if probe.aborted:
        print("run aborted: %d connection failures in a row" % MAX_CONNECTION_FAILURES, file=err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
