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
import errno
import ipaddress
import json
import socket
import urllib.error
import urllib.request

from gbox import models, netiface

class NetworkDown(ValueError):
    """Raised when a sweep found no route at all, as opposed to finding no miner."""


VENDOR = "Goldshell"
MAX_HOSTS = 1022            # a /22. Wider than that is a typo, not a home LAN.
DEFAULT_TIMEOUT = 1.5
DEFAULT_WORKERS = 32
MAX_FIELD = 40              # any host on the LAN can answer port 80; its strings go to a terminal
LINK_LOCAL = netiface.LINK_LOCAL

# A sweep of a network that is not there fails on every address at once, with no timeout to wait
# out. That is the machine saying the LAN is gone, and it deserves its own answer rather than
# "no miner found", which would send the owner looking for a miner that is probably fine.
DOWN_ERRNOS = frozenset({errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL})
DOWN_WINERRORS = frozenset({10050, 10051, 10065})      # WSAENETDOWN, WSAENETUNREACH, WSAEHOSTUNREACH


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


def local_subnet():
    """The one LAN this machine is on, as a CIDR string, read from the OS interface table.

    Every decision here belongs to `gbox.netiface`, which holds the hard rules: a tunnel is
    never a source of an address range, the prefix is the interface's own and never assumed,
    the routing table is never consulted, 169.254.x is never a network, a disconnected
    interface is not a network, and two candidate LANs are a question rather than a guess.

    Raises ValueError, with what the OS actually reported, when there is no single answer.
    """
    return netiface.lan_subnet()

def local_lan():
    """(cidr, interface name) for the one LAN this machine is on. Raises ValueError when unsure."""
    found = netiface.lan_networks()
    cidr = netiface.lan_subnet()          # raises with the OS's own reasons when there is no single answer
    name = next((n for net, n in found if str(net) == cidr), "?")
    return cidr, name


def outside(host, cidr):
    """True when `host` is a plain address that is not inside `cidr`. Unknown shapes are not a claim."""
    try:
        return ipaddress.IPv4Address(_host(host)) not in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def targets_for(cidr):
    """The usable host addresses of `cidr`, refusing a sweep too wide to be a home LAN.

    `strict=False`, so "192.168.1.50/24" is read as the /24 that holds it."""
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
    if net.subnet_of(LINK_LOCAL) or net.overlaps(LINK_LOCAL):
        # 169.254.0.0/16 is what an adapter gives itself when DHCP never answered. Nothing is on
        # the other side of it, by definition, so there is nothing there to find.
        raise ValueError("%s is link-local (169.254.0.0/16), which is what an adapter assigns itself "
                         "when DHCP does not answer. Nothing is reachable there." % net)
    for tunnel in netiface.tunnel_networks():
        if net.overlaps(tunnel):
            raise ValueError("%s is carried by a tunnel on this machine (%s). A VPN is not a LAN and "
                             "this will not sweep one, whoever asks: the range belongs to the far end, "
                             "not to your network." % (net, tunnel))
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
    kind, body = _probe(addr, timeout)
    return body if kind == "hit" else None


def _is_network_down(exc):
    """True when the OS said the network itself is not there, rather than this host being quiet."""
    seen = [exc]
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        seen.append(reason)
    for e in seen:
        if getattr(e, "errno", None) in DOWN_ERRNOS:
            return True
        if getattr(e, "winerror", None) in DOWN_WINERRORS:
            return True
    return False


def _probe(addr, timeout=DEFAULT_TIMEOUT):
    """("hit", dict), ("down", None) when the network is unreachable, or ("miss", None)."""
    req = urllib.request.Request("http://%s/mcb/status" % addr)      # no Authorization header, ever
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            body = json.loads(r.read(65536).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        return ("down" if _is_network_down(e) else "miss"), None
    except ValueError:
        return "miss", None
    if not isinstance(body, dict):
        return "miss", None
    model = _text(body.get("model"))
    if not model or not model.strip().casefold().startswith(VENDOR.casefold()):
        return "miss", None
    profile = models.profile_for(model)
    return "hit", {
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
        results = list(pool.map(lambda t: _probe(t, timeout), todo))
    hits = [body for kind, body in results if kind == "hit"]
    if not hits and results and all(kind == "down" for kind, _ in results):
        # Every address failed the same way, instantly: there is no route to that network at all.
        raise NetworkDown("the network %s is not reachable from this machine at all: every one of "
                          "the %d addresses failed immediately. Check the cable, the Wi-Fi or the "
                          "switch; nothing here says anything about the miner."
                          % (_range_of(todo), len(todo)))
    return sorted(hits, key=lambda h: sort_key(h["address"]))


def _range_of(targets):
    """A short way to name what was swept, for an error message."""
    if len(targets) == 1:
        return str(targets[0])
    return "%s to %s" % (targets[0], targets[-1])
