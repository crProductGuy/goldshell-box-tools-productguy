"""Which networks are this machine's own LANs, read from the OS and never guessed.

Eight hard rules govern this module. They are not preferences, and nothing in the
package may work around them.

1. **A tunnel is never a source of an address range.** VPN, tap, tun, PPP,
   WireGuard, Teredo, ISATAP and their kin are excluded by what the interface
   *is*, not by what address it carries. A corporate VPN hands out 10.x, which
   is as private as a home LAN and just as wrong to sweep: the test is the
   interface, never the range.
2. **The prefix is read from the interface, never assumed.** The router handed
   the machine an address and a prefix over DHCP; that prefix is the network.
   A /22 LAN is a /22, and a /27 is a /27. This module never appends /24 to
   anything.
3. **The routing table is never consulted.** The old "connect a UDP socket and
   see which source address the route picks" trick answers with the default
   route, which on a machine running a VPN client is the tunnel. It is not used
   here, and it must not come back.
4. **Ambiguity is reported, not resolved.** Two LAN interfaces on two different
   networks is a question for the operator, not a coin toss. It raises, names
   both, and asks for an explicit subnet.
5. **169.254.0.0/16 is never a network.** An APIPA address is the operating
   system saying DHCP never answered: the cable is out, the switch is dead, or
   the adapter is idle. By definition nothing is on the other side of it, so it
   is not a candidate, not a fallback, and not a thing to sweep. Windows also
   labels these `SuffixOrigin = Link`, and hands one to every idle adapter, so a
   machine with no LAN at all can show ten of them.
6. **A disconnected interface is not a network.** An address can outlive its
   link: the OS keeps the lease while the cable is out. Where the platform says
   whether the link is up and whether the address is still valid, both must say
   yes. Windows gives `MediaConnectionState` and `AddressState`, Linux gives
   `operstate` and `carrier`, the BSDs give `RUNNING` in the flags.
7. **A LAN that overlaps a tunnel is refused, not preferred.** When the VPN
   hands out the same range as the house router, which side a packet leaves by
   is not something this code can know. It says so and asks for an explicit
   subnet.
8. **Nothing here needs the internet, and nothing here may use it.** No DNS
   lookup, no name resolution, no reachability test against anything outside.
   A machine with its uplink down still knows its own LAN, and that is the only
   question being asked. A test asserts the module's source contains no socket
   calls at all.

LAN is LAN. No further reach.

Everything here reads the OS's own interface table: `Get-NetIPAddress` plus
`Get-NetAdapter` on Windows, `ip -4 -o addr` plus sysfs on Linux, `ifconfig -a`
on macOS and the BSDs. Nothing is contacted, nothing is sent, no name is
resolved.
"""
import base64
import ipaddress
import json
import os
import re
import subprocess
import sys

# Interface names, descriptions and driver component ids that mean "tunnel". The short ones are
# matched as whole words or as a leading token, so an "Intel(R) Ethernet" is not read as a tun.
TUNNEL_WORDS = ("tun", "tap", "wg", "ppp", "utun", "zt", "ipsec", "gpd", "nordlynx", "proton",
                "ip6tnl", "sit", "gre", "6to4",
                # A veth is a container's end of a pair, never a LAN. "br" is NOT here: br0 is the
                # real LAN on any machine whose owner bridged their NIC, and guessing there would
                # take a working setup away from the person most likely to have one.
                "veth", "virbr", "cni", "flannel")
TUNNEL_PHRASES = ("vpn", "wintun", "wireguard", "openvpn", "tunnel", "anyconnect", "globalprotect",
                  "tailscale", "zerotier", "hamachi", "softether", "forticlient", "pulse secure",
                  "wan miniport", "teredo", "isatap", "l2tp", "pptp", "sstp", "virtual private",
                  "point to point", "point-to-point",
                  # Hypervisor networks that look exactly like a LAN and reach no router: a
                  # host-only segment, a VMware vmnet, the Hyper-V Default Switch, the KM-TEST
                  # loopback. An EXTERNAL Hyper-V switch is a real LAN and is deliberately not here.
                  "host-only", "hostonly", "vmnet", "default switch", "km-test", "loopback adapter",
                  # Container and subsystem bridges: real Ethernet media, real private addresses, and
                  # they reach containers rather than the house. "vEthernet (WSL)" is one of these;
                  # "vEthernet (External)" is a genuine bridged LAN and is deliberately not matched.
                  "docker", "vethernet (wsl", "vethernet (nat", "podman", "libvirt")

# 169.254.0.0/16, RFC 3927. The OS assigns it to itself when DHCP does not answer; it means "this
# adapter is not on a network". Rule 5: never a candidate, never a fallback, never swept.
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")

# IANA ifType values that are tunnels by definition: PPP, propVirtual (what Wintun reports) and
# tunnel. Ethernet (6) and IEEE 802.11 (71) are the media a LAN actually arrives on.
TUNNEL_IFTYPES = frozenset({23, 53, 131})
LAN_MEDIA = ("802.3", "802.11", "native 802.11", "ethernet", "wireless")

_WORD = re.compile(r"[a-z]+")


def _tokens(text):
    """The alphabetic runs of a name, so "tun0" gives "tun" and "Fortune 500" never does.

    Digits are separators, not part of the word: interface names are `tun0`, `utun3`, `wg0`,
    `tap0901`. Matching on whole words is what keeps a "Fortune 500 GbE" from reading as a tunnel.
    """
    return _WORD.findall((text or "").lower())


def is_tunnel(iface):
    """True when this interface is a tunnel, by what it is rather than what address it holds.

    Checked in order of how much the OS actually knows: an explicit flag from the platform reader,
    then the IANA interface type, then a medium that is not a LAN medium, then the name, the driver
    description and the component id. A doubt resolves to True, because sweeping a tunnel is the
    failure this module exists to prevent and skipping one LAN candidate is not.
    """
    if iface.get("tunnel"):                       # the platform reader is certain (sysfs, PPP flags)
        return True
    if int(iface.get("iftype") or 0) in TUNNEL_IFTYPES:
        return True
    medium = (iface.get("medium") or "").strip().lower()
    if medium and not any(m in medium for m in LAN_MEDIA):
        return True
    words = set()
    for field in ("name", "description", "component"):
        words.update(_tokens(iface.get(field)))
        text = (iface.get(field) or "").lower()
        if any(phrase in text for phrase in TUNNEL_PHRASES):
            return True
    return bool(words & set(TUNNEL_WORDS))


def is_autoconfigured(iface):
    """True for an APIPA address: 169.254.x, or the platform saying DHCP never answered (rule 5).

    This is not a judgement call. An address in 169.254.0.0/16 means the adapter asked for a lease
    and got silence, so there is no router, no DHCP server and nothing else on that wire worth a
    request. Windows reports the same fact as `SuffixOrigin = Link`.
    """
    if iface.get("autoconfigured"):
        return True
    try:
        return ipaddress.IPv4Address(iface.get("address")) in LINK_LOCAL
    except (TypeError, ValueError):
        return False


def is_connected(iface):
    """Whether the link is up and the address still valid, as far as the platform will say (rule 6).

    Unknown counts as connected: on a platform that reports neither, the other rules still apply and
    refusing every interface would be worse than trusting the address table.
    """
    return bool(iface.get("connected", True)) and bool(iface.get("usable", True))


def network_of(iface):
    """The interface's own network, from its own address and its own prefix. None when unusable."""
    try:
        addr = ipaddress.IPv4Address(iface["address"])
        prefixlen = int(iface["prefixlen"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 8 <= prefixlen <= 32:
        return None
    if addr.is_loopback or addr.is_multicast or addr.is_unspecified:
        return None
    if addr in LINK_LOCAL or is_autoconfigured(iface):      # rule 5, by name rather than by side effect
        return None
    try:
        return ipaddress.ip_network("%s/%d" % (addr, prefixlen), strict=False)
    except ValueError:
        return None


# ------------------------------------------------------------------ platform readers

_WINDOWS_PS = """
$ad = @{}
foreach ($a in Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue) { $ad[[int]$a.InterfaceIndex] = $a }
$out = foreach ($ip in Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop) {
  $a = $ad[[int]$ip.InterfaceIndex]
  [pscustomobject]@{
    name        = [string]$ip.InterfaceAlias
    address     = [string]$ip.IPAddress
    prefixlen   = [int]$ip.PrefixLength
    iftype      = if ($a) { [int]$a.InterfaceType } else { 0 }
    medium      = if ($a) { [string]$a.MediaType } else { '' }
    hardware    = if ($a) { [bool]$a.HardwareInterface } else { $false }
    description = if ($a) { [string]$a.InterfaceDescription } else { '' }
    component   = if ($a) { [string]$a.ComponentID } else { '' }
    state       = [string]$ip.AddressState
    origin      = [string]$ip.SuffixOrigin
    link        = if ($a) { [string]$a.MediaConnectionState } else { '' }
  }
}
ConvertTo-Json -InputObject @($out) -Compress -Depth 3
"""


def _run(argv, timeout=20):
    """An OS command's stdout, or None. Never raises; a machine without the tool simply has no data."""
    try:
        out = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def parse_windows(text):
    """The rows of the PowerShell query above, as interface dicts."""
    try:
        rows = json.loads(text or "")
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        iface = {"name": r.get("name") or "", "address": r.get("address") or "",
                 "prefixlen": r.get("prefixlen"), "iftype": r.get("iftype") or 0,
                 "medium": r.get("medium") or "", "description": r.get("description") or "",
                 "component": r.get("component") or ""}
        state = (r.get("state") or "").strip().lower()
        link = (r.get("link") or "").strip().lower()
        if state:
            # Preferred is the only usable state. Tentative means duplicate-address detection has
            # not finished or failed, Deprecated and Invalid mean the lease is on its way out.
            iface["usable"] = state == "preferred"
        if link:
            iface["connected"] = link == "connected"
        if (r.get("origin") or "").strip().lower() == "link":
            iface["autoconfigured"] = True          # APIPA: Windows saying DHCP never answered
        out.append(iface)
    return out


def _windows_interfaces():
    encoded = base64.b64encode(_WINDOWS_PS.encode("utf-16-le")).decode("ascii")
    for exe in ("pwsh.exe", "powershell.exe"):       # PowerShell 7 where it exists, 5.1 everywhere
        text = _run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
        if text:
            rows = parse_windows(text)
            if rows:
                return rows
    return []


_IP_ADDR = re.compile(r"^\d+:\s+(?P<name>[^\s:@]+).*?\binet\s+(?P<addr>\d+\.\d+\.\d+\.\d+)/(?P<len>\d+)")


def parse_ip_addr(text, sysfs="/sys/class/net"):
    """`ip -4 -o addr show` output, one interface per line, with sysfs consulted for certainty.

    sysfs is where Linux says outright what a device is: a `tun_flags` file exists only on tun/tap
    devices, and a wireguard device says DEVTYPE=wireguard in its uevent. That beats any name.
    """
    out = []
    for line in (text or "").splitlines():
        m = _IP_ADDR.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        iface = {"name": name, "address": m.group("addr"), "prefixlen": int(m.group("len")),
                 "iftype": 0, "medium": "", "description": "", "component": ""}
        base = os.path.join(sysfs, name)
        if os.path.exists(os.path.join(base, "tun_flags")):
            iface["tunnel"] = True
        else:
            try:
                with open(os.path.join(base, "uevent"), encoding="utf-8", errors="replace") as f:
                    uevent = f.read().lower()
                if "devtype=wireguard" in uevent or "devtype=ppp" in uevent:
                    iface["tunnel"] = True
            except OSError:
                pass
            try:
                with open(os.path.join(base, "type"), encoding="utf-8") as f:
                    arphrd = int(f.read().strip())
                iface["medium"] = "802.3" if arphrd == 1 else ""
                if arphrd not in (1, 801, 802, 803):          # not Ethernet and not 802.11
                    iface["tunnel"] = True
            except (OSError, ValueError):
                pass
        # Rule 6: operstate and carrier are Linux saying whether anything is on the other end.
        try:
            with open(os.path.join(base, "operstate"), encoding="utf-8") as f:
                state = f.read().strip().lower()
            if state in ("down", "lowerlayerdown", "notpresent"):
                iface["connected"] = False
        except OSError:
            pass
        try:
            with open(os.path.join(base, "carrier"), encoding="utf-8") as f:
                if f.read().strip() == "0":
                    iface["connected"] = False
        except (OSError, ValueError):
            pass                        # carrier reads EINVAL on a down device; operstate already said so
        out.append(iface)
    return out


_IFCONFIG_HEAD = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+):\s+flags=\d+<(?P<flags>[^>]*)>")
_IFCONFIG_INET = re.compile(r"^\s*inet\s+(?P<addr>\d+\.\d+\.\d+\.\d+).*?netmask\s+(?P<mask>0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)")


def parse_ifconfig(text):
    """`ifconfig -a` on macOS and the BSDs. POINTOPOINT in the flags is a tunnel saying so itself."""
    out, name, flags = [], None, ""
    for line in (text or "").splitlines():
        head = _IFCONFIG_HEAD.match(line)
        if head:
            name, flags = head.group("name"), head.group("flags").upper()
            continue
        inet = _IFCONFIG_INET.match(line)
        if not inet or name is None:
            continue
        mask = inet.group("mask")
        try:
            bits = int(mask, 16) if mask.startswith("0x") else int(ipaddress.IPv4Address(mask))
            prefixlen = bin(bits).count("1")
        except ValueError:
            continue
        iface = {"name": name, "address": inet.group("addr"), "prefixlen": prefixlen,
                 "iftype": 0, "medium": "", "description": "", "component": ""}
        if "POINTOPOINT" in flags:
            iface["tunnel"] = True
        # Rule 6: UP is "configured", RUNNING is "something is actually on the wire". A cable pulled
        # out of a Mac leaves UP set and RUNNING clear, with the old address still in the table.
        if "RUNNING" not in flags:
            iface["connected"] = False
        out.append(iface)
    return out


def interfaces():
    """This machine's IPv4 interfaces as the OS reports them. Empty when the OS will not say."""
    if sys.platform.startswith("win"):
        return _windows_interfaces()
    if sys.platform.startswith("linux"):
        rows = parse_ip_addr(_run(["ip", "-4", "-o", "addr", "show"]))
        return rows or parse_ifconfig(_run(["ifconfig", "-a"]))
    return parse_ifconfig(_run(["ifconfig", "-a"]))


# ------------------------------------------------------------------ what the caller asks for


def _networks(want_tunnels, require_connected=True):
    seen, out = set(), []
    for iface in interfaces():
        net = network_of(iface)
        if net is None or is_tunnel(iface) != want_tunnels:
            continue
        if require_connected and not is_connected(iface):
            continue                      # rule 6: an address can outlive its link
        if not want_tunnels and not net.is_private:
            continue                      # a LAN is private; a public range on a local NIC is not ours to sweep
        if net not in seen:
            seen.add(net)
            out.append((net, iface.get("name") or "?"))
    return out


def lan_networks():
    """Every distinct private network this machine is directly attached to, tunnels excluded.

    Each entry is (network, interface name). The network's prefix is the interface's own, so a /22
    LAN comes back as a /22. A network that overlaps a tunnel's is left out: rule 7, because which
    way a packet leaves is then not something this code can know.
    """
    tunnels = [net for net, _ in _networks(want_tunnels=True, require_connected=False)]
    return [(net, name) for net, name in _networks(want_tunnels=False)
            if not any(net.overlaps(t) for t in tunnels)]


def tunnel_networks():
    """The networks reachable only through a tunnel. Named so that callers can refuse them."""
    return [net for net, _ in _networks(want_tunnels=True, require_connected=False)]


def why_no_lan():
    """One sentence on what the OS did report, for an error message that helps rather than shrugs."""
    every = interfaces()
    if not every:
        return "the OS reported no IPv4 interfaces at all"
    apipa = [i for i in every if is_autoconfigured(i)]
    tunnels = [i for i in every if is_tunnel(i) and not is_autoconfigured(i)]
    dark = [i for i in every if not is_connected(i) and not is_autoconfigured(i) and not is_tunnel(i)]
    overlapping = [net for net, _ in _networks(want_tunnels=False)
                   if any(net.overlaps(t) for t in tunnel_networks())]
    parts = []
    if overlapping:
        parts.append("%s overlaps a tunnel's own range, so which way a packet would leave is not knowable"
                     % ", ".join(str(n) for n in overlapping))
    if apipa:
        parts.append("%d address%s in 169.254.0.0/16, which means DHCP never answered on %s"
                     % (len(apipa), "" if len(apipa) == 1 else "es",
                        "that adapter" if len(apipa) == 1 else "those adapters"))
    if tunnels:
        parts.append("%d tunnel interface%s" % (len(tunnels), "" if len(tunnels) == 1 else "s"))
    if dark:
        parts.append("%d interface%s whose link is down or whose address is no longer valid"
                     % (len(dark), "" if len(dark) == 1 else "s"))
    return "; ".join(parts) or "nothing the OS reported is a connected, private, non-tunnel network"


def lan_subnet():
    """The one LAN this machine is on, as a CIDR string.

    Raises ValueError with something the operator can act on when there is no answer or more than
    one: guessing between two networks is exactly the assumption this module exists to refuse.
    """
    found = lan_networks()
    if not found:
        raise ValueError("no LAN found on this machine: %s. Connect the machine to its network, or "
                         "give --subnet." % why_no_lan())
    if len(found) > 1:
        listed = ", ".join("%s on %s" % (net, name) for net, name in found)
        raise ValueError("this machine is on more than one LAN (%s); say which with --subnet" % listed)
    return str(found[0][0])
