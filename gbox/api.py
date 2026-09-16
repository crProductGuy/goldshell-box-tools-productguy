"""One serialized session with the miner, plus the parsers for what it returns.

Two firmware quirks shape this module (details in docs/firmware-api.md):

- The token check has a race. With several requests in flight it answers 401
  to roughly one in 200. So every request goes through one lock, a 401 is
  retried before it is believed, and only then is the session re-logged-in.
- Request bursts crash the web backend. The lock also means a caller cannot
  accidentally fan out.

Nothing here logs a URL: the login URL carries the encrypted password.
"""
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import aes


class MinerError(Exception):
    """The miner could not be reached or answered with an error."""


class AuthError(MinerError):
    """Login rejected, or 401 persisted after retries and re-login."""


class NoCredentials(AuthError):
    """A request needs a token and the session has neither a token nor a password."""


# ---------------------------------------------------------------- parsers

def kv(text, key):
    """Value of `[key] => value` in cgminer-style text, or None."""
    m = re.search(r"\[" + re.escape(key) + r"\] => ([^\n]*)", text)
    return m.group(1).strip() if m else None


_MINERINFO_FIELDS = {
    "elapsed": "Device Elapsed", "mhs_av": "MHS av", "mhs_20s": "MHS 20s",
    "accepted": "Accepted", "rejected": "Rejected", "hw_errors": "Hardware Errors",
    "hw_pct": "Device Hardware%", "clock": "clock", "fan0": "fan0", "fan1": "fan1",
    "chip_temp": "tstemp-0", "chip_temp1": "tstemp-1", "board_temp": "tstemp-2",
    "rebootcnt": "rebootcnt", "overheat": "overheat",
}


def _num(s):
    if s is None or s == "":
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() and "." not in s else f


def parse_minerinfo(text):
    """The `/dbg/minerinfo` totals the tools use, as numbers (None when absent). One board: that board's
    fields, unchanged from every earlier release. Several: `board_totals` over every `[PGAn]` block."""
    return board_totals(parse_minerinfo_boards(text))


_PGA_RE = re.compile(r"^\[PGA(\d+)\] =>", re.M)


def _fan_list(read):
    """`read(n)` for fan0, fan1, ... until the first gap (fan0..fan7 at most)."""
    fans = []
    for n in range(8):
        v = read(n)
        if v is None:
            break
        fans.append(v)
    return fans


def _board_from_kv(block, unit_text, index):
    """One per-board record from a `[PGAn] =>` block (or, for a firmware that writes none, the whole text).
    `voltage`/`current` are unit-level: on the text transport they appear once, in `[STATUS]`, outside any
    `[PGAn]` block, so they are read from `unit_text` (the whole response) rather than from `block`."""
    b = {name: _num(kv(block, key)) for name, key in _MINERINFO_FIELDS.items()}
    b["board"] = index
    b["nonced"] = _num(kv(block, "Nonced"))
    b["fans"] = _fan_list(lambda n: _num(kv(block, "fan%d" % n)))
    b["voltage_mv"] = _num(kv(unit_text, "voltage"))
    b["current_ma"] = _num(kv(unit_text, "current"))
    return b


def parse_minerinfo_boards(text):
    """One dict per `[PGAn] =>` block of `/dbg/minerinfo`, in order. No block: one dict from the whole text
    (a firmware that writes no PGA headers still gets today's behaviour)."""
    heads = list(_PGA_RE.finditer(text))
    if not heads:
        return [_board_from_kv(text, text, 0)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append(_board_from_kv(text[m.start():end], text, int(m.group(1))))
    return out


def parse_devs4028(text):
    """The `devs` reply on port 4028 -> the same per-board list. Strips cgminer's trailing NUL. Unlike the
    text transport, `voltage`/`current` are repeated per board here, so each is read straight off its own
    DEVS entry."""
    j = json.loads(text.rstrip("\x00"))
    out = []
    for d in j.get("DEVS", []):
        g = lambda k: _num(None if d.get(k) is None else str(d.get(k)))
        b = {name: g(key) for name, key in _MINERINFO_FIELDS.items()}
        b["board"] = int(d.get("PGA", len(out)))
        b["nonced"] = g("Nonced")
        b["fans"] = _fan_list(lambda n: g("fan%d" % n))
        b["voltage_mv"] = g("voltage")
        b["current_ma"] = g("current")
        out.append(b)
    return out


def _hottest_index(boards):
    """Index of the board with the highest `chip_temp` (ties keep the earlier board); 0 when none is known."""
    best_i, best_t = 0, None
    for i, b in enumerate(boards):
        t = b["chip_temp"]
        if t is not None and (best_t is None or t > best_t):
            best_i, best_t = i, t
    return best_i


def board_totals(boards):
    """The dict every caller of `parse_minerinfo` uses. One board: that board's fields, unchanged. Several:
    sums for hashrates, shares and errors; `hw_pct` as errors over nonces (percent) when every board's nonce
    count is known, else the mean of the boards' own percentages; `elapsed` the max; `clock` the first
    non-None; `rebootcnt` and `overheat` the max; `chip_temp`, `chip_temp1` and `board_temp` from the
    HOTTEST board (max `chip_temp`), so the watchdog, the charts and log.csv's tstemp columns mean "the
    hottest board" on a big unit and exactly what they mean today on the SC-BOX; `fan0`/`fan1` the first two
    of the unit's fans. New keys: `fans` (list), `nboards`, `hot_board` (index), `watts_dc` (mV*mA/1e6, or
    None when either is unknown)."""
    if len(boards) == 1:
        out = {name: boards[0][name] for name in _MINERINFO_FIELDS}
    else:
        out = {}
        elapsed = [b["elapsed"] for b in boards if b["elapsed"] is not None]
        out["elapsed"] = max(elapsed) if elapsed else None
        for key in ("mhs_av", "mhs_20s", "accepted", "rejected", "hw_errors"):
            vals = [b[key] for b in boards if b[key] is not None]
            out[key] = sum(vals) if vals else None
        nonced = [b["nonced"] for b in boards]
        if all(n is not None for n in nonced) and sum(nonced) > 0 and \
                all(b["hw_errors"] is not None for b in boards):
            out["hw_pct"] = 100.0 * sum(b["hw_errors"] for b in boards) / sum(nonced)
        else:
            pcts = [b["hw_pct"] for b in boards if b["hw_pct"] is not None]
            out["hw_pct"] = (sum(pcts) / len(pcts)) if pcts else None
        out["clock"] = next((b["clock"] for b in boards if b["clock"] is not None), None)
        rebootcnt = [b["rebootcnt"] for b in boards if b["rebootcnt"] is not None]
        out["rebootcnt"] = max(rebootcnt) if rebootcnt else None
        overheat = [b["overheat"] for b in boards if b["overheat"] is not None]
        out["overheat"] = max(overheat) if overheat else None
        hot = boards[_hottest_index(boards)]
        out["chip_temp"] = hot["chip_temp"]
        out["chip_temp1"] = hot["chip_temp1"]
        out["board_temp"] = hot["board_temp"]
        fans = boards[0]["fans"]
        out["fan0"] = fans[0] if len(fans) > 0 else None
        out["fan1"] = fans[1] if len(fans) > 1 else None
    out["fans"] = boards[0]["fans"] if boards else []
    out["nboards"] = len(boards)
    out["hot_board"] = _hottest_index(boards)
    v, c = boards[0]["voltage_mv"], boards[0]["current_ma"]
    out["watts_dc"] = (v * c / 1e6) if (v is not None and c is not None) else None
    return out


_CHIPTEMP_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*?Chip Avgtemp (-?\d+(?:\.\d+)?)'C, MaxTemp (-?\d+(?:\.\d+)?)'C")


_BOOT_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\].*Init sucessed")


def last_boot_ts(text):
    """The miner timestamp of the newest `SCBOX Init sucessed` line in the log, or None. The log survives a
    power cycle, so readings older than this line belong to the run before the boot."""
    last = None
    for line in text.splitlines():
        m = _BOOT_RE.match(line)
        if m:
            last = m.group(1)
    return last


def parse_chiptemps(text, after=None):
    """The `/dbg/minersyslog` temperature lines as (miner_timestamp, chip_avg, chip_max) tuples, in log order.

    One line every 5 s: ` [2026-09-15 07:36:21] C0: Chip Avgtemp 69.000000'C, MaxTemp 79.000000'C`.
    The timestamp is the miner's own clock (only good for ordering); `after` keeps lines newer than
    that timestamp. Nothing but numbers and timestamps leaves this function: the log repeats the
    pool user, so its text is never stored or logged by anything that calls it.
    """
    out = []
    for line in text.splitlines():
        m = _CHIPTEMP_RE.match(line)
        if not m:
            continue
        ts = m.group(1)
        if after is not None and ts <= after:
            continue
        out.append((ts, float(m.group(2)), float(m.group(3))))
    return out


def parse_icinfo(text):
    """`/dbg/icinfo` -> list of boards, each a list of {chip, good, bad}."""
    body = json.loads(json.loads(text)["body"])
    return [[{"chip": c["chipindex"], "good": c["perf"], "bad": c["hwerr"]} for c in board]
            for board in body["drawdata"]]


def format_chips(boards):
    """Every chip's cumulative counts for the log's `chips` column: `board.chip:good/bad` joined by `;`,
    in board then chip order. Board-aware from the first row so a multi-board unit needs no second format."""
    return ";".join("%d.%d:%d/%d" % (b, c["chip"], c["good"], c["bad"])
                    for b, board in enumerate(boards) for c in board)


_CHIPS_RE = re.compile(r"^(\d+\.\d+):(\d+)/(\d+)$")


def parse_chips(text):
    """The `chips` column back into {"board.chip": (good, bad)}; junk entries are skipped, "" is {}."""
    out = {}
    for part in (text or "").split(";"):
        m = _CHIPS_RE.match(part.strip())
        if m:
            out[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return out


def chip_health(chip, best_good):
    """One of ok, weak, failing, judged relative to the best chip on the board."""
    total = chip["good"] + chip["bad"]
    bad_pct = 100.0 * chip["bad"] / total if total else 0.0
    if chip["good"] < 0.3 * best_good or bad_pct > 30:
        return "failing"
    if chip["good"] < 0.7 * best_good or chip["bad"] > 40:
        return "weak"
    return "ok"


def weak_chips(board):
    """Chips that are weak or failing, judged relative to the best chip on the board."""
    if not board:
        return []
    best = max(c["good"] for c in board)
    return [c for c in board if chip_health(c, best) != "ok"]


# The power plan string in every dialect seen so far (docs/firmware-api.md, "Power plan dialects"):
#   box       575 MHz 0.41 V 90 RPM 90 RPM             the SC-BOX, read from the unit
#   mv_pv     625 MHz 9100 V 40 RPM 40 RPM PV 9400     the SC Lite (fw 2.2.0), from the other developer's notes
#   float_pv  750 MHz 0.41 V 50 RPM 50 RPM PV 9400     the documented "float-V / optional-PV" form of the HS Box
_PLAN_RE = re.compile(r"^\s*(\d+)\s*MHz\s+([\d.]+)\s*V\s+(\d+)\s*RPM\s+(\d+)\s*RPM(?:\s+PV\s+(\d+))?\s*$")


def parse_plan(plan):
    """A power plan string into its parts, in whichever dialect the firmware wrote it.

    Returns {mhz, volts, fan_a, fan_b, pv, volts_text, dialect}. `volts` is the number as written (the SC Lite's
    9100 stays 9100.0: nobody has confirmed it is millivolts, and nothing here needs to know); `volts_text` is
    the token verbatim so `format_plan(**parse_plan(s))` gives `s` back; `pv` is None without a PV term.
    """
    m = _PLAN_RE.match(plan if isinstance(plan, str) else "")
    if not m:
        raise ValueError("not a power plan string: %r" % (plan,))
    volts_text = m.group(2)
    try:
        volts = float(volts_text)
    except ValueError:
        raise ValueError("not a power plan string: %r" % (plan,)) from None
    pv = int(m.group(5)) if m.group(5) is not None else None
    dialect = "box" if pv is None else ("float_pv" if "." in volts_text else "mv_pv")
    return {"mhz": int(m.group(1)), "volts": volts, "fan_a": int(m.group(3)), "fan_b": int(m.group(4)),
            "pv": pv, "volts_text": volts_text, "dialect": dialect}


def format_plan(mhz, volts, fan_a, fan_b, pv=None, volts_text=None, dialect=None):
    """The plan string back. Positional use writes the box form; `volts_text` (from parse_plan) is written
    verbatim, else volts is trimmed as the BOX writes it (0.4, not 0.40); a `pv` adds the PV term. `dialect`
    is accepted so `format_plan(**parse_plan(s))` works; the parts decide the form, not the label."""
    if volts_text is None:
        volts_text = ("%.2f" % volts).rstrip("0").rstrip(".")
    text = "%d MHz %s V %d RPM %d RPM" % (mhz, volts_text, fan_a, fan_b)
    return text + (" PV %d" % pv if pv is not None else "")


def with_mhz(plan, mhz, volts=None):
    """`plan` with only its clock changed (and its volts, when given); every other token as the firmware wrote
    it, whatever the dialect. This is what the clock control sends, so a unit never gets a plan in a form it
    did not write itself."""
    parts = parse_plan(plan)
    parts["mhz"] = mhz
    if volts is not None:
        parts["volts"], parts["volts_text"] = volts, None
    return format_plan(**parts)


def max_preset_mhz(setting):
    """Highest clock among the firmware presets; the top of the sane range."""
    best = 0
    for p in setting.get("powerplans", []):
        try:
            best = max(best, parse_plan(p.get("info"))["mhz"])
        except ValueError:
            pass
    return best


def hashrate_unit(mhs):
    """Pick a display unit for a value in MH/s. Returns (unit name, divisor)."""
    v = mhs or 0
    if v >= 1e6:
        return ("TH/s", 1000000)
    if v >= 1e3:
        return ("GH/s", 1000)
    return ("MH/s", 1)


# ---------------------------------------------------------------- session

class Miner:
    """Serialized HTTP session with one miner.

    Credentials are any one of: a password, its encrypted hex form, or a
    ready token. Without any, requests raise NoCredentials.
    """

    RETRY_401 = 2          # retries of a single request before the 401 is believed
    RETRY_DELAY = 0.7      # seconds, grows linearly

    def __init__(self, host, password=None, password_hex=None, token=None, timeout=15):
        if not host:
            raise ValueError("miner host is required")
        self.host = host
        self.base = host if host.startswith("http") else "http://" + host
        self.timeout = timeout
        self._password_hex = aes.encrypt_password(password) if password else password_hex
        self._token = token
        self._lock = threading.Lock()
        self.request_count = 0
        self.unauthorized_count = 0

    # -- credentials

    @property
    def has_token(self):
        return bool(self._token)

    @property
    def can_login(self):
        return bool(self._password_hex)

    @property
    def has_credentials(self):
        return self.has_token or self.can_login

    def set_token(self, token):
        if not isinstance(token, str) or token.count(".") != 2:
            raise ValueError("not a JWT")
        self._token = token

    def forget_token(self):
        self._token = None

    def login(self):
        """Exchange the password for a token. Serialized like every other request."""
        with self._lock:
            return self._login_locked()

    def verify_password_hex(self, password_hex):
        """True when the miner accepts this encrypted password. AuthError when it rejects it (or the
        input is not one), MinerError when it does not answer. The session's own token is untouched.

        Used by the service for the power buttons: the page proves the password the way the clock
        button does, by a login, but the service is the one holding the plug.
        """
        try:
            raw = bytes.fromhex(password_hex) if isinstance(password_hex, str) else b""
        except ValueError:
            raw = b""
        if not raw or len(raw) % 16:
            raise AuthError("password check: not an encrypted password")
        with self._lock:
            self._login_locked(password_hex, store=False)
        return True

    def _login_locked(self, password_hex=None, store=True):
        hexpw = password_hex or self._password_hex
        if not hexpw:
            raise NoCredentials("no password to log in with")
        url = "%s/user/login?%s" % (self.base, urllib.parse.urlencode(
            {"username": "admin", "password": hexpw, "cipher": "true"}))
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise AuthError("login failed: HTTP %d" % e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise MinerError("login: %s" % _describe(e)) from None
        tok = body.get("JWT Token") if isinstance(body, dict) else None
        if not isinstance(tok, str) or tok.count(".") != 2:
            raise AuthError("login rejected (wrong password?)")
        if store:
            self._token = tok
        return tok

    # -- raw requests

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, body=None):
        return self.request("PUT", path, body)

    def request(self, method, path, body=None):
        """One request, serialized, with 401 retry and one re-login. Returns the body text."""
        with self._lock:
            if not self._token:
                self._login_locked()
            relogged = False
            attempt = 0
            while True:
                status, text = self._send_locked(method, path, body)
                if status != 401:
                    return text
                self.unauthorized_count += 1
                if attempt < self.RETRY_401:
                    attempt += 1
                    time.sleep(self.RETRY_DELAY * attempt)
                    continue
                if self.can_login and not relogged:
                    relogged = True
                    attempt = 0
                    self._login_locked()
                    continue
                self._token = None
                raise AuthError("miner rejected the session token")

    def _send_locked(self, method, path, body):
        self.request_count += 1
        data = None
        headers = {"Authorization": "Bearer " + self._token}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request("%s/%s" % (self.base, path.lstrip("/")), data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return 401, ""
            raise MinerError("%s %s: HTTP %d" % (method, path, e.code)) from None
        except (urllib.error.URLError, OSError) as e:
            raise MinerError("%s %s: %s" % (method, path, _describe(e))) from None

    # -- typed reads

    def status(self):
        return json.loads(self.get("mcb/status"))

    def setting(self):
        return json.loads(self.get("mcb/setting"))

    def minerinfo(self):
        return parse_minerinfo(self.get("dbg/minerinfo"))

    def boards(self):
        return parse_icinfo(self.get("dbg/icinfo"))

    def history(self):
        return json.loads(self.get("cpb/hshistory"))

    def syslog(self):
        """The cgminer log as text (about 2 MB; the firmware truncates it near 3 MB). Never persist it."""
        return self.get("dbg/minersyslog")

    # -- writes

    def set_plan(self, mhz, volts=None):
        """Set the clock live via the manual power plan. Returns the plan string applied."""
        st = self.setting()
        current = st.get("manualPowerplan")
        try:
            parse_plan(current)
        except ValueError:
            current = st["powerplans"][0]["info"]
            parse_plan(current)
        top = max_preset_mhz(st) or 725
        if mhz % 25 or not 300 <= mhz <= top:
            raise ValueError("clock must be a multiple of 25 between 300 and %d MHz" % top)
        st["manual"] = True
        st["manualPowerplan"] = with_mhz(current, mhz, volts)     # the unit's own dialect, only the clock changed
        self.put("mcb/setting", st)
        return self.setting().get("manualPowerplan")

    def set_fan_target(self, temp):
        st = self.setting()
        lo, hi = (st.get("temp_targets") or [65, 75])[:2]
        if not lo <= temp <= hi:
            raise ValueError("fan target must be between %g and %g C on this firmware" % (lo, hi))
        st["temp_target"] = temp
        self.put("mcb/setting", st)
        return self.setting().get("temp_target")

    def restart(self):
        self.put("mcb/restart")


def _describe(exc):
    """Error text that never includes a URL."""
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return str(reason)
    return "%s: %s" % (type(exc).__name__, exc)
