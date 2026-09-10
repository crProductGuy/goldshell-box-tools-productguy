"""Smart plugs: the driver interface, the Kasa legacy driver, and LAN discovery.

The watchdog uses a plug for the one thing a soft restart cannot do: cut
power to a controller that has frozen. The dashboard only reads plug state
and watts; nothing here is reachable from the page.

Drivers implement five calls (identify, state, watts, off, on) and are
tested against a fake. `cycle` is off, wait, on, and attempts `on` even
when `off` raised, so a failed cycle never leaves the miner dark on
purpose.

Kasa legacy protocol (HS100/103/105/110, KP115/125, EP10, HS300 on
firmware before the 2023-2025 updates): TCP port 9999, JSON commands
obfuscated with an autokey XOR starting at 171, framed by a 4-byte
big-endian length; UDP discovery on 9999 is the same bytes without the
length. There is no authentication. Newer firmware moved to an encrypted
HTTP protocol (KLAP) on port 80 with the TP-Link account credentials;
discovery reports those devices so `gbox power discover` can say so, but
this module does not talk to them.

Never log a plug's sysinfo blob: it carries the cloud account name.
"""
import json
import socket
import struct
import time

_XOR_KEY = 171
LEGACY_PORT = 9999
KLAP_DISCOVERY_PORT = 20002
_KLAP_PROBE = bytes.fromhex("020000010000000000000000463cb5d3")


class PlugError(Exception):
    """The plug did not answer, answered nonsense, or refused."""


def xor_encrypt(text):
    key, out = _XOR_KEY, bytearray()
    for b in text.encode("utf-8"):
        key ^= b
        out.append(key)
    return bytes(out)


def xor_decrypt(data):
    key, out = _XOR_KEY, bytearray()
    for b in data:
        out.append(key ^ b)
        key = b
    return out.decode("utf-8", errors="replace")


def _split_host(host, default_port):
    if ":" in host and not host.startswith("["):
        h, _, p = host.rpartition(":")
        return h, int(p)
    return host, default_port


def _watts_from(realtime):
    """Watts from an emeter get_realtime reply in either field shape, or None when unsupported."""
    if not realtime or realtime.get("err_code", 0) != 0:
        return None
    if "power_mw" in realtime:
        return realtime["power_mw"] / 1000.0
    if "power" in realtime:
        return float(realtime["power"])
    return None


class Plug:
    """What every driver provides. Subclasses fill in the five calls."""

    name = "plug"

    def identify(self):
        raise NotImplementedError

    def state(self):
        raise NotImplementedError

    def watts(self):
        raise NotImplementedError

    def off(self):
        raise NotImplementedError

    def on(self):
        raise NotImplementedError

    def cycle(self, off_seconds, sleep=time.sleep):
        """Off, wait, on. If `off` raises, `on` is still attempted before the error propagates."""
        try:
            self.off()
        except Exception:
            try:
                self.on()
            except Exception:
                pass
            raise
        sleep(off_seconds)
        self.on()

    def describe(self):
        info = self.identify()
        w = self.watts() if info.get("meter") else None
        return "%s, %s, %s" % (info.get("model", self.name), "on" if self.state() else "off",
                               ("%.0f W" % w) if w is not None else "no meter")


class KasaLegacy(Plug):
    name = "kasa"

    def __init__(self, host, timeout=5.0):
        self.host, self.port = _split_host(host, LEGACY_PORT)
        self.timeout = float(timeout)

    def _query(self, cmd):
        payload = xor_encrypt(json.dumps(cmd))
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(struct.pack(">I", len(payload)) + payload)
                hdr = _read_exactly(s, 4)
                body = _read_exactly(s, struct.unpack(">I", hdr)[0])
        except (OSError, socket.timeout) as e:
            raise PlugError("plug at %s did not answer: %s" % (self.host, type(e).__name__)) from None
        try:
            return json.loads(xor_decrypt(body))
        except ValueError:
            raise PlugError("plug at %s sent an unreadable reply" % self.host) from None

    def _sysinfo(self):
        info = self._query({"system": {"get_sysinfo": {}}}).get("system", {}).get("get_sysinfo", {})
        if info.get("err_code", 0) != 0:
            raise PlugError("plug refused get_sysinfo (err_code %s)" % info.get("err_code"))
        if "relay_state" not in info:
            raise PlugError("plug reports no single relay (a multi-outlet strip?); not supported")
        return info

    def identify(self):
        info = self._sysinfo()
        return {
            "model": info.get("model", "?"), "alias": info.get("alias", ""), "device_id": info.get("deviceId", ""),
            "hw": info.get("hw_ver", "?"), "fw": info.get("sw_ver", "?"),
            "meter": self.watts() is not None,
        }

    def state(self):
        return bool(self._sysinfo().get("relay_state"))

    def watts(self):
        return _watts_from(self._query({"emeter": {"get_realtime": {}}}).get("emeter", {}).get("get_realtime", {}))

    def _relay(self, on):
        r = self._query({"system": {"set_relay_state": {"state": 1 if on else 0}}})
        code = r.get("system", {}).get("set_relay_state", {}).get("err_code", -1)
        if code != 0:
            raise PlugError("plug refused set_relay_state (err_code %s)" % code)

    def off(self):
        self._relay(False)

    def on(self):
        self._relay(True)


def _read_exactly(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed early")
        buf += chunk
    return buf


DRIVERS = {"kasa": KasaLegacy}


def make(power_cfg, timeout=5.0):
    """A driver from a config `power` block. Unknown driver: ValueError (caught at validation time)."""
    driver = power_cfg.get("driver", "kasa")
    if driver not in DRIVERS:
        raise ValueError("unknown plug driver %r (known: %s)" % (driver, ", ".join(sorted(DRIVERS))))
    return DRIVERS[driver](power_cfg["host"], timeout=timeout)


def _local_broadcasts():
    """The subnet broadcast of every local IPv4 address we can find, plus the limited broadcast."""
    targets = ["255.255.255.255"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                targets.append(ip.rsplit(".", 1)[0] + ".255")      # assumes a /24; good enough for a home LAN
    except OSError:
        pass
    return tuple(dict.fromkeys(targets))


def discover(timeout=3.0, port=LEGACY_PORT, klap_port=KLAP_DISCOVERY_PORT, targets=None):
    """Plugs on the LAN, one dict per address: host, model, alias, device_id, relay, meter, watts, protocol."""
    targets = targets or _local_broadcasts()
    found = {}
    # legacy: get_sysinfo and the meter in one datagram
    msg = xor_encrypt(json.dumps({"system": {"get_sysinfo": {}}, "emeter": {"get_realtime": {}}}))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        for t in targets:
            try:
                s.sendto(msg, (t, port))
            except OSError:
                continue
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(8192)
            except (socket.timeout, OSError):
                break
            if addr[0] in found:
                continue
            try:
                r = json.loads(xor_decrypt(data))
            except ValueError:
                continue
            si = r.get("system", {}).get("get_sysinfo", {})
            found[addr[0]] = {
                "host": addr[0], "protocol": "legacy", "model": si.get("model", "?"), "alias": si.get("alias", ""),
                "device_id": si.get("deviceId", ""), "relay": bool(si.get("relay_state")) if "relay_state" in si else None,
                "watts": _watts_from(r.get("emeter", {}).get("get_realtime")), "hw": si.get("hw_ver", "?"), "fw": si.get("sw_ver", "?"),
            }
            found[addr[0]]["meter"] = found[addr[0]]["watts"] is not None
    # newer firmware: answers a fixed probe on 20002 with a JSON body after a 16-byte header
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        for t in targets:
            try:
                s.sendto(_KLAP_PROBE, (t, klap_port))
            except OSError:
                continue
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(8192)
            except (socket.timeout, OSError):
                break
            if addr[0] in found:
                continue
            try:
                res = json.loads(data[16:]).get("result", {})
            except ValueError:
                continue
            scheme = res.get("mgt_encrypt_schm") or {}
            found[addr[0]] = {
                "host": addr[0], "protocol": (scheme.get("encrypt_type") or "klap").lower(), "model": res.get("device_model", "?"),
                "alias": "", "device_id": res.get("device_id", ""), "relay": None, "watts": None, "meter": False,
                "hw": res.get("hw_ver", "?"), "fw": res.get("fw_ver", "?"),
            }
    return [found[k] for k in sorted(found)]
