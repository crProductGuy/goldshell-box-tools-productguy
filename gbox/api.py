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
    """The `/dbg/minerinfo` fields the tools use, as numbers (None when absent)."""
    return {name: _num(kv(text, key)) for name, key in _MINERINFO_FIELDS.items()}


def parse_icinfo(text):
    """`/dbg/icinfo` -> list of boards, each a list of {chip, good, bad}."""
    body = json.loads(json.loads(text)["body"])
    return [[{"chip": c["chipindex"], "good": c["perf"], "bad": c["hwerr"]} for c in board]
            for board in body["drawdata"]]


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


_PLAN_RE = re.compile(r"^\s*(\d+)\s*MHz\s+([\d.]+)\s*V\s+(\d+)\s*RPM\s+(\d+)\s*RPM\s*$")


def parse_plan(plan):
    """Turn a plan string such as 600 MHz 0.41 V 90 RPM 90 RPM into (600, 0.41, 90, 90)."""
    m = _PLAN_RE.match(plan or "")
    if not m:
        raise ValueError("not a power plan string: %r" % (plan,))
    return int(m.group(1)), float(m.group(2)), int(m.group(3)), int(m.group(4))


def format_plan(mhz, volts, fan_a, fan_b):
    return "%d MHz %s V %d RPM %d RPM" % (mhz, ("%.2f" % volts).rstrip("0").rstrip("."), fan_a, fan_b)


def max_preset_mhz(setting):
    """Highest clock among the firmware presets; the top of the sane range."""
    best = 0
    for p in setting.get("powerplans", []):
        try:
            best = max(best, parse_plan(p.get("info"))[0])
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

    def _login_locked(self):
        if not self._password_hex:
            raise NoCredentials("no password to log in with")
        url = "%s/user/login?%s" % (self.base, urllib.parse.urlencode(
            {"username": "admin", "password": self._password_hex, "cipher": "true"}))
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

    # -- writes

    def set_plan(self, mhz, volts=None):
        """Set the clock live via the manual power plan. Returns the plan string applied."""
        st = self.setting()
        try:
            _, cur_v, fan_a, fan_b = parse_plan(st.get("manualPowerplan"))
        except ValueError:
            _, cur_v, fan_a, fan_b = parse_plan(st["powerplans"][0]["info"])
        top = max_preset_mhz(st) or 725
        if mhz % 25 or not 300 <= mhz <= top:
            raise ValueError("clock must be a multiple of 25 between 300 and %d MHz" % top)
        st["manual"] = True
        st["manualPowerplan"] = format_plan(mhz, volts if volts is not None else cur_v, fan_a, fan_b)
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
