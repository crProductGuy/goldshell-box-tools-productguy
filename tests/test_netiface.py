"""The LAN, read from the OS and never guessed, with tunnels excluded by what they are.

The hard rules of `gbox/netiface.py` are what these tests hold: a tunnel is never a source of an
address range, the prefix comes from the interface, the routing table is never consulted, 169.254 is
never a network, a disconnected link is not a network, a LAN overlapping a tunnel is refused, and
two candidate LANs are a question rather than a coin toss.

The Windows fixture is the real shape of `Get-NetIPAddress` joined to `Get-NetAdapter`, captured on
a Windows 11 machine running an OpenVPN client, with the names and addresses changed: a public
tunnel address became 198.51.100.x and the LAN became 192.168.50.x. The Linux and macOS fixtures
are written from those tools' documented output, not captured, and are labelled as such where it
matters.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from gbox import netiface

# One OpenVPN tunnel (Wintun, propVirtual, /27), one real Ethernet NIC (/24), the loopback, and the
# leftover link-local tunnel adapters such a client scatters across a Windows machine.
WINDOWS_ROWS = [
    {"name": "TheVPN_OVPN", "address": "198.51.100.70", "prefixlen": 27, "iftype": 53,
     "medium": "IP", "hardware": False, "description": "TheVPN_OVPN Tunnel", "component": "Wintun"},
    {"name": "Ethernet", "address": "192.168.50.182", "prefixlen": 24, "iftype": 6,
     "medium": "802.3", "hardware": True, "description": "Realtek Gaming GbE Family Controller",
     "component": "PCI\\VEN_10EC&DEV_8168"},
    {"name": "Loopback Pseudo-Interface 1", "address": "127.0.0.1", "prefixlen": 8, "iftype": 0,
     "medium": "", "hardware": False, "description": "", "component": ""},
    {"name": "Local Area Connection 8", "address": "169.254.247.134", "prefixlen": 16, "iftype": 53,
     "medium": "IP", "hardware": False, "description": "Wintun Userspace Tunnel #7", "component": "Wintun"},
]


class WindowsReaderTest(unittest.TestCase):
    def rows(self):
        return netiface.parse_windows(json.dumps(WINDOWS_ROWS))

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_the_query_output_parses_into_one_dict_per_address(self):
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["address"], "192.168.50.182")
        self.assertEqual(rows[1]["prefixlen"], 24)

    def test_a_single_object_rather_than_an_array_is_still_read(self):
        """ConvertTo-Json collapses a one-element array into an object on older PowerShell."""
        self.assertEqual(len(netiface.parse_windows(json.dumps(WINDOWS_ROWS[1]))), 1)

    def test_garbage_or_silence_from_the_shell_is_no_interfaces_not_an_exception(self):
        for text in ("", None, "not json", "[1, 2, 3]"):
            self.assertEqual(netiface.parse_windows(text), [])

    def test_the_tunnel_is_recognised_and_the_ethernet_is_not(self):
        rows = self.rows()
        self.assertTrue(netiface.is_tunnel(rows[0]))
        self.assertFalse(netiface.is_tunnel(rows[1]))
        self.assertTrue(netiface.is_tunnel(rows[3]))

    def test_the_lan_is_the_ethernet_with_the_prefix_the_adapter_reports(self):
        self.use(self.rows())
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")
        self.assertEqual([str(n) for n, _ in netiface.lan_networks()], ["192.168.50.0/24"])

    def test_the_tunnels_network_is_reported_separately_so_a_caller_can_refuse_it(self):
        self.use(self.rows())
        self.assertEqual([str(n) for n in netiface.tunnel_networks()], ["198.51.100.64/27"])


class TheHardRulesTest(unittest.TestCase):
    """The rules this module exists for. Each of these is a bug that shipped or nearly shipped."""

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_a_vpn_handing_out_a_private_address_is_still_never_a_lan(self):
        """The rule that address-based checks got wrong: 10.8.0.x is as private as a home LAN.

        A public tunnel address made the first version of this look safe. A corporate or WireGuard
        VPN hands out RFC1918, passes every is_private test, and is exactly as wrong to sweep.
        """
        self.use([
            {"name": "OpenVPN TAP", "address": "10.8.0.6", "prefixlen": 24, "iftype": 6,
             "medium": "802.3", "description": "TAP-Windows Adapter V9", "component": "tap0901"},
            {"name": "Ethernet", "address": "192.168.50.182", "prefixlen": 24, "iftype": 6,
             "medium": "802.3", "description": "Intel(R) Ethernet Connection", "component": "PCI\\X"},
        ])
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")
        self.assertEqual([str(n) for n in netiface.tunnel_networks()], ["10.8.0.0/24"])

    def test_a_wireguard_interface_with_a_private_address_is_a_tunnel_too(self):
        self.use([
            {"name": "wg0", "address": "10.13.13.2", "prefixlen": 24, "iftype": 0, "medium": ""},
            {"name": "eth0", "address": "192.168.50.7", "prefixlen": 24, "iftype": 0, "medium": "802.3"},
        ])
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")

    def test_the_prefix_is_the_interfaces_own_and_never_a_24(self):
        """A /22 LAN is a /22. The version this replaces appended /24 to whatever it found."""
        self.use([{"name": "eth0", "address": "192.168.52.9", "prefixlen": 22, "iftype": 0,
                   "medium": "802.3"}])
        self.assertEqual(netiface.lan_subnet(), "192.168.52.0/22")
        self.use([{"name": "eth0", "address": "192.168.50.9", "prefixlen": 27, "iftype": 0,
                   "medium": "802.3"}])
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/27")

    def test_nothing_in_this_module_consults_the_routing_table(self):
        """Rule 3, held as source: the UDP-connect trick answered with the default route, which on a
        machine running a VPN client is the tunnel. It must not come back."""
        source = Path(netiface.__file__).read_text(encoding="utf-8")
        for banned in ("SOCK_DGRAM", "connect(", "getsockname", "gethostname", "getaddrinfo"):
            self.assertNotIn(banned, source, "netiface must not reach the network or the route table")

    def test_two_lans_are_a_question_not_a_coin_toss(self):
        self.use([
            {"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24, "medium": "802.3"},
            {"name": "Wi-Fi", "address": "10.0.9.7", "prefixlen": 24, "medium": "native 802.11"},
        ])
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        said = str(e.exception)
        self.assertIn("192.168.50.0/24", said)
        self.assertIn("10.0.9.0/24", said)
        self.assertIn("--subnet", said)

    def test_two_interfaces_on_the_same_lan_are_one_answer(self):
        self.use([
            {"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24, "medium": "802.3"},
            {"name": "Wi-Fi", "address": "192.168.50.8", "prefixlen": 24, "medium": "native 802.11"},
        ])
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")

    def test_no_lan_at_all_asks_for_a_subnet_rather_than_guessing(self):
        self.use([{"name": "TheVPN", "address": "198.51.100.70", "prefixlen": 27, "iftype": 53,
                   "medium": "IP", "component": "Wintun"}])
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        self.assertIn("--subnet", str(e.exception))

    def test_an_os_that_says_nothing_is_a_refusal_not_a_guess(self):
        self.use([])
        with self.assertRaises(ValueError):
            netiface.lan_subnet()

    def test_a_public_address_on_a_real_nic_is_not_swept_either(self):
        """A machine with a routable address on its NIC is somebody's server, not a home LAN."""
        self.use([{"name": "Ethernet", "address": "93.184.216.7", "prefixlen": 24, "medium": "802.3"}])
        with self.assertRaises(ValueError):
            netiface.lan_subnet()

    def test_loopback_and_link_local_are_never_a_lan(self):
        self.use([
            {"name": "lo", "address": "127.0.0.1", "prefixlen": 8, "medium": ""},
            {"name": "Ethernet 2", "address": "169.254.7.7", "prefixlen": 16, "medium": "802.3"},
        ])
        with self.assertRaises(ValueError):
            netiface.lan_subnet()


class NameClassificationTest(unittest.TestCase):
    """A short token like "tun" matches as a word, so an ordinary NIC is not mistaken for a tunnel."""

    def test_the_usual_tunnel_names_are_caught(self):
        for name in ("tun0", "tap0", "utun3", "wg0", "ppp0", "ipsec0", "TheVPN_OVPN", "nordlynx",
                     "ZeroTier One [abc]", "Tailscale", "WAN Miniport (PPTP)"):
            self.assertTrue(netiface.is_tunnel({"name": name}), name)

    def test_an_ordinary_adapter_is_not(self):
        for name, desc in (("Ethernet", "Realtek Gaming GbE Family Controller"),
                           ("Wi-Fi", "Intel(R) Wi-Fi 6 AX200 160MHz"),
                           ("eth0", "Fortune 500 GbE"),          # holds "tun" inside a word
                           ("enp3s0", "Intel Corporation I211"),
                           ("Ethernet 3", "ASIX AX88179 USB 3.0 to Gigabit Ethernet")):
            self.assertFalse(netiface.is_tunnel({"name": name, "description": desc}), name)

    def test_a_medium_that_is_not_a_lan_medium_is_a_tunnel(self):
        self.assertTrue(netiface.is_tunnel({"name": "Local Area Connection 5", "medium": "IP"}))
        self.assertFalse(netiface.is_tunnel({"name": "Local Area Connection 5", "medium": "802.3"}))

    def test_an_unknown_medium_leaves_the_decision_to_the_other_signals(self):
        self.assertFalse(netiface.is_tunnel({"name": "eth0", "medium": ""}))


class LinuxReaderTest(unittest.TestCase):
    """`ip -4 -o addr show`, with sysfs consulted. Output shape from the iproute2 documentation."""

    SAMPLE = (
        "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
        "2: eth0    inet 192.168.50.182/24 brd 192.168.50.255 scope global dynamic eth0\\       valid_lft 84used\n"
        "5: tun0    inet 10.8.0.6/24 scope global tun0\\       valid_lft forever preferred_lft forever\n"
        "7: wg0    inet 10.13.13.2/32 scope global wg0\\       valid_lft forever preferred_lft forever\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sysfs = Path(self.tmp.name)
        for name, arphrd, extra in (("lo", 772, None), ("eth0", 1, None), ("tun0", 65534, "tun_flags"),
                                    ("wg0", 65534, "uevent")):
            d = self.sysfs / name
            d.mkdir()
            (d / "type").write_text("%d\n" % arphrd, encoding="utf-8")
            if extra == "tun_flags":
                (d / "tun_flags").write_text("0x1002\n", encoding="utf-8")
            elif extra == "uevent":
                (d / "uevent").write_text("DEVTYPE=wireguard\nINTERFACE=wg0\n", encoding="utf-8")

    def rows(self):
        return netiface.parse_ip_addr(self.SAMPLE, sysfs=str(self.sysfs))

    def test_every_address_is_read_with_its_own_prefix(self):
        rows = self.rows()
        self.assertEqual([(r["name"], r["prefixlen"]) for r in rows],
                         [("lo", 8), ("eth0", 24), ("tun0", 24), ("wg0", 32)])

    def test_sysfs_settles_what_a_name_only_suggests(self):
        by_name = {r["name"]: r for r in self.rows()}
        self.assertTrue(by_name["tun0"].get("tunnel"))       # tun_flags exists only on tun/tap
        self.assertTrue(by_name["wg0"].get("tunnel"))        # DEVTYPE=wireguard in uevent
        self.assertFalse(by_name["eth0"].get("tunnel"))

    def test_the_lan_is_the_ethernet(self):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = self.rows
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")

    def test_a_missing_sysfs_does_not_break_the_read(self):
        rows = netiface.parse_ip_addr(self.SAMPLE, sysfs=os.path.join(self.tmp.name, "nothing-here"))
        self.assertEqual(len(rows), 4)
        self.assertTrue(netiface.is_tunnel(rows[2]))         # the name still says tun0


class MacReaderTest(unittest.TestCase):
    """`ifconfig -a` on macOS. Shape from the man page and the usual utun VPN layout."""

    SAMPLE = (
        "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
        "\tinet 192.168.50.182 netmask 0xffffff00 broadcast 192.168.50.255\n"
        "en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
        "\tinet 172.20.4.9 netmask 0xfffffc00 broadcast 172.20.7.255\n"
        "utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380\n"
        "\tinet 10.8.0.2 --> 10.8.0.2 netmask 0xffffff00\n"
    )

    def rows(self):
        return netiface.parse_ifconfig(self.SAMPLE)

    def test_a_hex_netmask_becomes_a_prefix_length(self):
        by_name = {r["name"]: r for r in self.rows()}
        self.assertEqual(by_name["en0"]["prefixlen"], 24)
        self.assertEqual(by_name["en5"]["prefixlen"], 22)     # 0xfffffc00, and it stays a /22
        self.assertEqual(by_name["lo0"]["prefixlen"], 8)

    def test_pointopoint_in_the_flags_is_a_tunnel_saying_so_itself(self):
        by_name = {r["name"]: r for r in self.rows()}
        self.assertTrue(by_name["utun3"].get("tunnel"))
        self.assertFalse(by_name["en0"].get("tunnel"))

    def test_two_wired_lans_on_a_mac_are_still_a_question(self):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = self.rows
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        self.assertIn("172.20.4.0/22", str(e.exception))


class ThisMachineTest(unittest.TestCase):
    """Reads the machine the suite runs on. Asserts only what must hold everywhere, so it passes on
    a laptop, a CI container with no LAN at all, and the Windows box this was written on."""

    def test_reading_the_interfaces_never_raises_and_never_invents_a_prefix(self):
        for iface in netiface.interfaces():
            self.assertIn("address", iface)
            net = netiface.network_of(iface)
            if net is not None:
                self.assertEqual(str(net.network_address), str(net.network_address))
                self.assertTrue(1 <= net.prefixlen <= 32)

    def test_no_lan_answer_is_ever_a_tunnel_network(self):
        tunnels = set(netiface.tunnel_networks())
        for net, _ in netiface.lan_networks():
            self.assertNotIn(net, tunnels)

    def test_whatever_it_answers_is_private_or_it_refuses(self):
        try:
            answer = netiface.lan_subnet()
        except ValueError:
            return
        import ipaddress
        self.assertTrue(ipaddress.ip_network(answer).is_private)


class NotConnectedTest(unittest.TestCase):
    """Rules 5 and 6: an address is not a network. The cases that come up when the LAN is gone."""

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_an_apipa_address_is_never_a_candidate_even_when_it_is_the_only_one(self):
        """169.254.x means DHCP never answered: there is nothing on that wire to find."""
        self.use([{"name": "Ethernet", "address": "169.254.7.7", "prefixlen": 16, "medium": "802.3",
                   "connected": True, "usable": True}])
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        said = str(e.exception)
        self.assertIn("169.254.0.0/16", said)
        self.assertIn("DHCP", said)

    def test_the_platform_saying_autoconfigured_is_enough_on_its_own(self):
        """Windows reports SuffixOrigin = Link for an APIPA lease; believe it whatever the address."""
        self.assertTrue(netiface.is_autoconfigured({"address": "10.0.0.5", "autoconfigured": True}))
        self.assertIsNone(netiface.network_of({"address": "10.0.0.5", "prefixlen": 24,
                                               "autoconfigured": True}))

    def test_every_idle_adapter_holding_a_169_254_address_is_still_no_lan(self):
        """A Windows machine with the cable out shows one of these per adapter, and none is a LAN."""
        self.use([{"name": "Local Area Connection %d" % i, "address": "169.254.%d.9" % i,
                   "prefixlen": 16, "medium": "802.3"} for i in range(1, 11)])
        with self.assertRaises(ValueError):
            netiface.lan_subnet()
        self.assertEqual(netiface.lan_networks(), [])

    def test_a_private_address_on_a_dead_link_is_not_a_lan(self):
        """The lease outlives the cable: the address table still shows it, the switch is gone."""
        self.use([{"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24,
                   "medium": "802.3", "connected": False, "usable": True}])
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        self.assertIn("link is down", str(e.exception))

    def test_an_address_that_is_not_preferred_is_not_used(self):
        for state in ({"usable": False}, {"usable": False, "connected": True}):
            iface = dict({"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24,
                          "medium": "802.3"}, **state)
            self.use([iface])
            with self.assertRaises(ValueError):
                netiface.lan_subnet()

    def test_a_live_lan_beside_ten_dead_adapters_is_still_found(self):
        """The common real state of a Windows box: one cable in, everything else idle."""
        rows = [{"name": "Local Area Connection %d" % i, "address": "169.254.%d.9" % i,
                 "prefixlen": 16, "medium": "IP", "connected": False} for i in range(1, 11)]
        rows.append({"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24,
                     "medium": "802.3", "connected": True, "usable": True})
        self.use(rows)
        self.assertEqual(netiface.lan_subnet(), "192.168.50.0/24")


class TunnelOverlapTest(unittest.TestCase):
    """Rule 7: when the VPN hands out the same range as the house router, nothing here can know
    which way a packet leaves, so it refuses instead of picking."""

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_a_lan_that_overlaps_a_tunnel_is_refused_with_a_reason(self):
        self.use([
            {"name": "Ethernet", "address": "192.168.8.182", "prefixlen": 24, "medium": "802.3"},
            {"name": "wg0", "address": "192.168.8.9", "prefixlen": 24, "medium": ""},
        ])
        with self.assertRaises(ValueError) as e:
            netiface.lan_subnet()
        said = str(e.exception)
        self.assertIn("overlaps", said)
        self.assertIn("192.168.8.0/24", said)

    def test_a_tunnel_on_a_different_range_does_not_disturb_the_lan(self):
        self.use([
            {"name": "Ethernet", "address": "192.168.8.182", "prefixlen": 24, "medium": "802.3"},
            {"name": "wg0", "address": "10.13.13.2", "prefixlen": 24, "medium": ""},
        ])
        self.assertEqual(netiface.lan_subnet(), "192.168.8.0/24")

    def test_a_down_tunnel_still_blocks_an_overlapping_lan(self):
        """A VPN that is configured but not up must not make the overlap look resolved."""
        self.use([
            {"name": "Ethernet", "address": "192.168.8.182", "prefixlen": 24, "medium": "802.3"},
            {"name": "tun0", "address": "192.168.8.9", "prefixlen": 24, "connected": False},
        ])
        with self.assertRaises(ValueError):
            netiface.lan_subnet()


class WindowsStateFieldsTest(unittest.TestCase):
    """The three fields the Windows query carries for rules 5 and 6."""

    def row(self, **kw):
        base = {"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24, "iftype": 6,
                "medium": "802.3", "hardware": True, "description": "", "component": "",
                "state": "Preferred", "origin": "Dhcp", "link": "Connected"}
        base.update(kw)
        return netiface.parse_windows(json.dumps([base]))[0]

    def test_a_dhcp_preferred_address_on_a_connected_adapter_is_usable(self):
        iface = self.row()
        self.assertTrue(netiface.is_connected(iface))
        self.assertFalse(netiface.is_autoconfigured(iface))

    def test_tentative_deprecated_and_invalid_are_not_usable(self):
        for state in ("Tentative", "Deprecated", "Invalid", "Duplicate"):
            self.assertFalse(netiface.is_connected(self.row(state=state)), state)

    def test_a_disconnected_adapter_is_not_connected(self):
        self.assertFalse(netiface.is_connected(self.row(link="Disconnected")))

    def test_suffix_origin_link_is_read_as_autoconfigured(self):
        self.assertTrue(netiface.is_autoconfigured(self.row(origin="Link", address="169.254.3.3")))
        self.assertTrue(netiface.is_autoconfigured(self.row(origin="Link")))

    def test_a_platform_that_says_nothing_is_treated_as_connected(self):
        """Rule 6 stops short of refusing everything on a platform that will not say."""
        self.assertTrue(netiface.is_connected({"name": "eth0", "address": "192.168.50.7"}))


class DegradedLinuxAndMacTest(unittest.TestCase):
    """The same two rules where Linux and the BSDs report them."""

    def test_operstate_down_is_not_connected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sysfs = Path(tmp.name)
        (sysfs / "eth0").mkdir()
        (sysfs / "eth0" / "type").write_text("1\n", encoding="utf-8")
        (sysfs / "eth0" / "operstate").write_text("down\n", encoding="utf-8")
        (sysfs / "eth0" / "carrier").write_text("0\n", encoding="utf-8")
        rows = netiface.parse_ip_addr("2: eth0    inet 192.168.50.7/24 scope global eth0",
                                      sysfs=str(sysfs))
        self.assertFalse(netiface.is_connected(rows[0]))

    def test_a_mac_interface_that_is_up_but_not_running_is_not_connected(self):
        text = ("en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 192.168.50.7 netmask 0xffffff00\n"
                "en1: flags=8822<UP,BROADCAST,SMART,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 192.168.60.7 netmask 0xffffff00\n")
        by_name = {r["name"]: r for r in netiface.parse_ifconfig(text)}
        self.assertTrue(netiface.is_connected(by_name["en0"]))
        self.assertFalse(netiface.is_connected(by_name["en1"]))      # cable out, address still listed

    def test_an_apipa_address_from_ifconfig_is_refused_like_any_other(self):
        text = ("en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 169.254.30.4 netmask 0xffff0000\n")
        rows = netiface.parse_ifconfig(text)
        self.assertTrue(netiface.is_autoconfigured(rows[0]))
        self.assertIsNone(netiface.network_of(rows[0]))




class ContainerAndSubsystemBridgeTest(unittest.TestCase):
    """Docker, WSL and libvirt bridges carry private addresses on Ethernet media and reach containers
    rather than the house. A bridged NIC does reach the house, and must survive."""

    def tunnel(self, name, description=""):
        return netiface.is_tunnel({"name": name, "description": description, "medium": "802.3"})

    def test_container_and_subsystem_bridges_are_not_lans(self):
        for name, desc in (("docker0", "bridge"), ("veth1a2b", "veth"), ("virbr0", "libvirt bridge"),
                           ("vEthernet (WSL (Hyper-V firewall))", "Hyper-V Virtual Ethernet Adapter"),
                           ("vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter")):
            self.assertTrue(self.tunnel(name, desc), name)

    def test_a_bridged_nic_is_still_the_lan(self):
        """br0 is the real network on any machine whose owner bridged their NIC, and an EXTERNAL
        Hyper-V switch is the same thing on Windows. Guessing here would break the setup of the
        person most likely to have one."""
        self.assertFalse(self.tunnel("br0", "Bridge"))
        self.assertFalse(self.tunnel("vEthernet (External)", "Hyper-V Virtual Ethernet Adapter"))
        self.assertFalse(self.tunnel("bond0", "Link aggregate"))


if __name__ == "__main__":
    unittest.main()
