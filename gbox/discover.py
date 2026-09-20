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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A sweep contacts the addresses it was given and no others.

    urllib follows redirects by default, so one LAN device answering the probe with a Location header
    could otherwise point the sweep at an address outside the range being swept. Returning None makes
    urllib raise the 3xx as an HTTPError, which `probe` already reads as a miss."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _split(addr):
    """("host", port or None) out of "host", "host:port", "http://host/path" and the like.

    `config.json`'s `host` is never validated or normalised, so it may carry a scheme or a trailing
    path. The skip list is matched against it, and a skip that silently stops matching would put a
    request on the configured miner while `gbox serve` is polling it."""
    text = addr if isinstance(addr, str) else ""
    text = text.split("://", 1)[-1].split("/", 1)[0].strip()
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


def _routed_address():
    """The address the default route would send from.

    No packet is sent: `connect` on a UDP socket only picks the route, and the route is what names
    the local address. The target is TEST-NET-1, which is reserved and unrouted, so nothing is
    contacted even if the call were to send."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def private_addresses():
    """Every private IPv4 this machine holds, the link-local ones last.

    `getaddrinfo` on the machine's own name returns the interface addresses. 169.254.x sorts last
    because Windows hands one to every idle adapter, and none of them is ever the miner's network."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            try:
                ip = ipaddress.IPv4Address(info[4][0])
            except ValueError:
                continue
            if ip.is_private and not ip.is_loopback and ip not in found:
                found.append(ip)
    except OSError:
        pass
    found.sort(key=lambda ip: (ip.is_link_local, int(ip)))
    return [str(ip) for ip in found]


def local_subnet():
    """The /24 of this machine's own LAN address, as a CIDR string.

    The default route is asked first, because on an ordinary machine that is the LAN. It is not
    always: a VPN client takes the default route, and then the routed address is the provider's, in
    a public range, with the miner nowhere near it. Sweeping that would send one request per address
    into a stranger's network through the tunnel, so a routed address that is not private is
    discarded and the machine's own private addresses are used instead. Found on the machine this
    was built on, 2026-09-20: the route named an address belonging to a VPN provider, in a public
    range, while the miner sat on an ordinary private LAN.

    Raises ValueError when no private address can be found, because guessing is worse than asking
    for --subnet."""
    routed = _routed_address()
    try:
        ip = ipaddress.IPv4Address(routed)
        if ip.is_private and not ip.is_loopback:
            return str(ipaddress.ip_network(routed + "/24", strict=False))
    except ValueError:
        pass
    for addr in private_addresses():
        return str(ipaddress.ip_network(addr + "/24", strict=False))
    raise ValueError("cannot tell which network to sweep from this machine's own addresses (the "
                     "default route is %s, which is not a private network); give --subnet" % routed)


def targets_for(cidr):
    """The usable host addresses of `cidr`, refusing a sweep too wide to be a home LAN.

    `strict=False`, so "192.168.8.148/24" is read as the /24 that holds it."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError as e:
        raise ValueError("not a subnet: %r (%s)" % (cidr, e))
    if net.version != 4:
        raise ValueError("%s is IPv6 and the probe speaks IPv4 only; give an IPv4 --subnet" % net)
    if not net.is_private:
        raise ValueError("%s is not a private network. This sweeps a home LAN, and a public range is a "
                         "typo here: it would send one request per address to somebody else's network "
                         "from your own address." % net)
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        raise ValueError("%s holds %d hosts and %d is the most this will sweep; "
                         "give a narrower --subnet." % (net, len(hosts), MAX_HOSTS))
    return hosts


def probe(addr, timeout=DEFAULT_TIMEOUT):
    """One tokenless GET /mcb/status against one address.

    Redirects are not followed and a 3xx is a miss.

    A JSON object whose `model` names the vendor is a hit and comes back as a
    dict; anything else, including every error and every timeout, is a miss and
    comes back as None. No credential is read, stored or sent."""
    req = urllib.request.Request("http://%s/mcb/status" % addr)      # no Authorization header, ever
    try:
        with _OPENER.open(req, timeout=timeout) as r:
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
