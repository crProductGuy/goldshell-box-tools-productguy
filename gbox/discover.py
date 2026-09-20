"""`gbox discover`: find a Goldshell miner on the local network.

One tokenless `GET /mcb/status` per address. The firmware answers that path
with no `Authorization` header (verified on the SC-BOX, 2026-09-19;
unverified on the SC5 Pro II), so a sweep reads no credential out of the
config and sends none. Port 4028 answers tokenless on both units but carries
no model string, so it is not the probe.

Nothing here logs a per-address result. A sweep of a /24 prints one line per
hit, never 254 lines saying nothing was there.

The configured miner is skipped by default and reported as skipped: a running
`gbox serve` may have a request in flight to it, and the firmware wants one
caller at a time. `--include-configured` overrides that, for when the service
is stopped. The one-request-per-miner rule is per miner, not per sweep, so
the thread pool is safe: it fans out across different hosts and each host is
asked exactly once.
"""
import concurrent.futures
import ipaddress
import json
import socket
import urllib.error
import urllib.request

from gbox import models

VENDOR = "Goldshell"
MAX_HOSTS = 1022            # a /22. Wider than that is a typo, not a home LAN.
DEFAULT_TIMEOUT = 1.5
DEFAULT_WORKERS = 32
MAX_FIELD = 40              # any host on the LAN can answer port 80; its strings go to a terminal


def _split(addr):
    """("host", port or None) out of "host" or "host:port"."""
    text = addr if isinstance(addr, str) else ""
    if text.count(":") == 1:
        host, _, port = text.rpartition(":")
        if port.isdigit():
            return host, int(port)
    return text, None


def _host(addr):
    """The host part of "host" or "host:port"; anything else unchanged."""
    return _split(addr)[0]


def _names(target, entry):
    """True when `entry` names `target`. Either side without a port matches any port on that host,
    because on a real LAN both are bare addresses; the ports only ever differ in the test suite,
    where two fake miners share 127.0.0.1."""
    t_host, t_port = _split(target)
    e_host, e_port = _split(entry)
    return t_host == e_host and (t_port is None or e_port is None or t_port == e_port)


def _text(value, limit=MAX_FIELD):
    """A string field out of the miner's untrusted JSON: printable, single-line and short, or None.

    A sweep asks every address on the subnet, and whatever answers decides what these strings hold.
    Printing an escape sequence straight to the operator's terminal is not a risk worth carrying for
    a field that only ever names a model."""
    if not isinstance(value, str):
        return None
    clean = "".join(ch if ch.isprintable() else " " for ch in value).strip()
    return clean[:limit] or None


def sort_key(addr):
    """Sort IPv4 addresses numerically, so .2 comes before .10, and put anything else after them."""
    try:
        return (0, int(ipaddress.IPv4Address(_host(addr))), "")
    except ValueError:                      # AddressValueError is a ValueError
        return (1, 0, str(addr))


def local_subnet():
    """This machine's own /24 as a CIDR string.

    No packet is sent: `connect` on a UDP socket only picks the route, and the
    route is what names the local address. The target is TEST-NET-1, which is
    reserved and unrouted, so nothing is contacted even if the call were to
    send."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            addr = s.getsockname()[0]
        except OSError:
            addr = "127.0.0.1"
    return str(ipaddress.ip_network(addr + "/24", strict=False))


def targets_for(cidr):
    """The usable host addresses of `cidr`, refusing a sweep too wide to be a home LAN.

    `strict=False`, so "192.168.8.148/24" is read as the /24 that holds it."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError as e:
        raise ValueError("not a subnet: %r (%s)" % (cidr, e))
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        raise ValueError("%s holds %d hosts and %d is the most this will sweep; "
                         "give a narrower --subnet." % (net, len(hosts), MAX_HOSTS))
    return hosts


def probe(addr, timeout=DEFAULT_TIMEOUT):
    """One tokenless GET /mcb/status against one address.

    A JSON object whose `model` names the vendor is a hit and comes back as a
    dict; anything else, including every error and every timeout, is a miss and
    comes back as None. No credential is read, stored or sent."""
    req = urllib.request.Request("http://%s/mcb/status" % addr)      # no Authorization header, ever
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read(65536).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    model = _text(body.get("model"))
    if not model or not model.strip().casefold().startswith(VENDOR.casefold()):
        return None
    profile = models.profile_for(model)
    return {
        "address": addr,
        "model": model,
        "name": profile["name"] if profile["known"] else "unknown model",
        "firmware": _text(body.get("firmware")),
        "hardware": _text(body.get("hardware")),
        "mcbversion": _text(body.get("mcbversion")),
    }


def sweep(targets, timeout=DEFAULT_TIMEOUT, workers=DEFAULT_WORKERS, skip=()):
    """Probe every target once and return the hits, sorted by address.

    `skip` is matched on the host part, so a configured "10.0.0.5:8080" skips
    the target "10.0.0.5"."""
    entries = list(skip or ())
    todo = [t for t in targets if not any(_names(t, e) for e in entries)]
    if not todo:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
        hits = [h for h in pool.map(lambda t: probe(t, timeout), todo) if h]
    return sorted(hits, key=lambda h: sort_key(h["address"]))
