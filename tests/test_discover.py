"""The LAN sweep: one tokenless GET /mcb/status per address, against fakes only.

No test here touches the real LAN. `sweep` is given an explicit target list in
every case, and `targets_for` is exercised on its arithmetic, never probed.
"""
import ipaddress
import unittest
from pathlib import Path

from gbox import discover, netiface
from tests.fake_miner import FakeMiner

DEAD = "127.0.0.1:1"          # nothing listens on port 1


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)

    def test_a_goldshell_unit_is_a_hit(self):
        hit = discover.probe(self.fm.address, timeout=5)
        self.assertEqual(hit["address"], self.fm.address)
        self.assertEqual(hit["model"], "Goldshell-SCBox")
        self.assertEqual(hit["firmware"], "2.2.5")
        self.assertEqual(hit["hardware"], "40.40.HA")

    def test_a_hit_names_the_profile_for_a_model_in_the_table(self):
        self.assertEqual(discover.probe(self.fm.address, timeout=5)["name"], "SC-BOX")

    def test_a_hit_says_unknown_model_for_a_goldshell_not_in_the_table(self):
        self.fm.status = dict(self.fm.status, model="Goldshell-KD-Max-9000")
        self.assertEqual(discover.probe(self.fm.address, timeout=5)["name"], "unknown model")

    def test_the_probe_neither_logs_in_nor_sends_a_token(self):
        discover.probe(self.fm.address, timeout=5)
        self.assertEqual(self.fm.logins, 0)
        self.assertEqual([h for _, _, h in self.fm.seen_headers], [None])

    def test_a_dead_port_is_a_miss(self):
        self.assertIsNone(discover.probe(DEAD, timeout=0.5))

    def test_json_from_something_that_is_not_a_goldshell_is_a_miss(self):
        self.fm.status = {"model": "Acme-Box", "firmware": "1.0"}
        self.assertIsNone(discover.probe(self.fm.address, timeout=5))

    def test_a_body_that_is_not_a_json_object_is_a_miss(self):
        for body in (["Goldshell-SCBox"], "Goldshell-SCBox", 7, None):
            self.fm.status = body
            self.assertIsNone(discover.probe(self.fm.address, timeout=5), body)

    def test_a_model_that_is_not_a_string_is_a_miss(self):
        self.fm.status = {"model": {"Goldshell": 1}}
        self.assertIsNone(discover.probe(self.fm.address, timeout=5))


class UntrustedBodyTest(unittest.TestCase):
    """Anything on the LAN can answer port 80. A sweep prints what it is told, so what it is told is cleaned."""

    ESC, CR, BEL = chr(27), chr(13), chr(7)

    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)

    def test_control_characters_never_reach_the_caller(self):
        self.fm.status = dict(self.fm.status,
                              model="Goldshell" + self.ESC + "[2J" + self.CR + "-SCBox",
                              firmware="2.2.5" + self.BEL)
        hit = discover.probe(self.fm.address, timeout=5)
        self.assertTrue(all(ch.isprintable() for ch in hit["model"]), repr(hit["model"]))
        self.assertNotIn(self.ESC, hit["model"])
        self.assertNotIn(self.BEL, hit["firmware"])

    def test_a_very_long_model_string_is_cut(self):
        self.fm.status = dict(self.fm.status, model="Goldshell-" + "A" * 500)
        self.assertLessEqual(len(discover.probe(self.fm.address, timeout=5)["model"]), discover.MAX_FIELD)

    def test_a_model_that_is_only_control_characters_is_a_miss(self):
        self.fm.status = dict(self.fm.status, model=self.ESC * 3)
        self.assertIsNone(discover.probe(self.fm.address, timeout=5))


class SweepTest(unittest.TestCase):
    def setUp(self):
        self.a = FakeMiner().start()
        self.b = FakeMiner().start()
        self.addCleanup(self.a.stop)
        self.addCleanup(self.b.stop)

    def test_two_units_and_a_dead_port(self):
        hits = discover.sweep([self.a.address, DEAD, self.b.address], timeout=1.0)
        self.assertEqual({h["address"] for h in hits}, {self.a.address, self.b.address})

    def test_each_unit_is_asked_exactly_once(self):
        discover.sweep([self.a.address, self.b.address], timeout=1.0)
        for fm in (self.a, self.b):
            self.assertEqual([p for _, p in fm.requests], ["/mcb/status"])

    def test_a_skipped_address_is_never_asked(self):
        hits = discover.sweep([self.a.address, self.b.address], timeout=1.0, skip=[self.a.address])
        self.assertEqual([h["address"] for h in hits], [self.b.address])
        self.assertEqual(self.a.requests, [])

    def test_a_skip_matches_on_the_host_when_the_target_carries_no_port(self):
        self.assertEqual(discover.sweep(["127.0.0.1"], timeout=0.5, skip=["127.0.0.1:8765"]), [])

    def test_an_empty_target_list_is_no_hits_and_no_error(self):
        self.assertEqual(discover.sweep([], timeout=1.0), [])


class SortTest(unittest.TestCase):
    def test_addresses_sort_numerically_not_lexicographically(self):
        got = sorted(["192.168.8.10", "192.168.8.2:80", "10.0.0.7"], key=discover.sort_key)
        self.assertEqual(got, ["10.0.0.7", "192.168.8.2:80", "192.168.8.10"])

    def test_something_unparseable_sorts_last_without_raising(self):
        got = sorted(["miner.local", "192.168.8.2"], key=discover.sort_key)
        self.assertEqual(got, ["192.168.8.2", "miner.local"])


class TargetsTest(unittest.TestCase):
    def test_a_24_is_its_254_usable_hosts(self):
        t = discover.targets_for("192.168.8.0/24")
        self.assertEqual(len(t), 254)
        self.assertEqual(t[0], "192.168.8.1")
        self.assertEqual(t[-1], "192.168.8.254")

    def test_a_host_address_is_accepted_and_read_as_its_network(self):
        self.assertEqual(discover.targets_for("192.168.8.148/24"), discover.targets_for("192.168.8.0/24"))

    def test_the_widest_allowed_sweep_is_1022_hosts(self):
        self.assertEqual(len(discover.targets_for("10.1.0.0/22")), discover.MAX_HOSTS)

    def test_anything_wider_is_refused_and_the_message_says_how_wide(self):
        with self.assertRaises(ValueError) as caught:
            discover.targets_for("10.1.0.0/21")
        self.assertIn("2046", str(caught.exception))
        self.assertIn(str(discover.MAX_HOSTS), str(caught.exception))

    def test_a_bad_cidr_is_refused(self):
        for bad in ("", "not a subnet", "192.168.8.0/33", "999.1.1.1/24", None):
            with self.assertRaises(ValueError):
                discover.targets_for(bad)

    def test_a_31_and_a_32_still_yield_the_addresses_they_hold(self):
        self.assertEqual(discover.targets_for("192.168.8.5/32"), ["192.168.8.5"])


class LocalSubnetTest(unittest.TestCase):
    def test_this_machine_is_in_the_24_it_reports(self):
        net = ipaddress.ip_network(discover.local_subnet())
        self.assertEqual(net.prefixlen, 24)
        self.assertGreaterEqual(len(discover.targets_for(str(net))), 254)




class PrivateNetworksOnlyTest(unittest.TestCase):
    """A sweep is a home-LAN tool. Refuse anything else, because a typo should not send 254 requests
    to somebody else's network from the operator's own address."""

    def test_the_private_ranges_a_home_lan_uses_are_allowed(self):
        for cidr in ("192.168.8.0/24", "10.1.2.0/24", "172.16.9.0/24", "127.0.0.0/24"):
            self.assertTrue(discover.targets_for(cidr))

    def test_a_public_range_is_refused_and_the_message_says_why(self):
        for cidr in ("8.8.8.0/24", "1.1.1.0/24", "93.184.216.0/24"):
            with self.assertRaises(ValueError) as e:
                discover.targets_for(cidr)
            self.assertIn("private", str(e.exception))

    def test_ipv6_is_refused_outright_rather_than_sweeping_255_addresses_that_cannot_be_probed(self):
        with self.assertRaises(ValueError) as e:
            discover.targets_for("fd00::/120")
        self.assertIn("IPv4", str(e.exception))

    def test_the_machines_own_subnet_is_always_acceptable_to_itself(self):
        discover.targets_for(discover.local_subnet())


class ConfiguredHostMatchingTest(unittest.TestCase):
    """The skip list is matched against whatever `config.json` holds, which is not always a bare address."""

    def test_a_configured_host_carrying_a_scheme_or_a_path_still_matches_the_bare_address(self):
        for entry in ("http://192.168.8.148", "http://192.168.8.148/", "192.168.8.148/mcb", "https://192.168.8.148:80"):
            self.assertTrue(discover._names("192.168.8.148", entry), entry)

    def test_a_different_address_is_not_matched_by_a_scheme(self):
        self.assertFalse(discover._names("192.168.8.149", "http://192.168.8.148"))


class RedirectTest(unittest.TestCase):
    """A device that answers the probe with a redirect must not send the sweep somewhere else.

    urllib follows redirects by default, so without a handler one LAN device could point the sweep at
    an address the operator never asked it to contact.
    """

    def setUp(self):
        import http.server
        import threading
        elsewhere = []

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                elsewhere.append(self.path)
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/somewhere-else")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.seen = elsewhere
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.addr = "127.0.0.1:%d" % self.srv.server_address[1]

    def test_a_redirect_is_a_miss_and_is_not_followed(self):
        self.assertIsNone(discover.probe(self.addr, timeout=5))
        self.assertEqual(self.seen, ["/mcb/status"])       # asked once, and nothing was chased


class LocalSubnetIsReadFromTheOsTest(unittest.TestCase):
    """`local_subnet` no longer decides anything: it asks `netiface`, which reads the OS.

    The route-based version this replaces asked the default route which network to sweep, and on a
    machine running a VPN client the default route is the tunnel. The rules now live in netiface and
    are tested there; what matters here is that discover delegates and does not second-guess.
    """

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_it_returns_what_the_interface_table_says_prefix_and_all(self):
        self.use([{"name": "Ethernet", "address": "192.168.52.9", "prefixlen": 22, "medium": "802.3"}])
        self.assertEqual(discover.local_subnet(), "192.168.52.0/22")

    def test_a_tunnel_is_never_the_answer_even_carrying_a_private_address(self):
        self.use([
            {"name": "tun0", "address": "10.8.0.6", "prefixlen": 24, "medium": ""},
            {"name": "Ethernet", "address": "192.168.50.7", "prefixlen": 24, "medium": "802.3"},
        ])
        self.assertEqual(discover.local_subnet(), "192.168.50.0/24")

    def test_no_lan_asks_for_a_subnet_rather_than_guessing(self):
        self.use([{"name": "Ethernet", "address": "169.254.9.9", "prefixlen": 16, "medium": "802.3"}])
        with self.assertRaises(ValueError) as e:
            discover.local_subnet()
        self.assertIn("--subnet", str(e.exception))

    def test_nothing_in_discover_reaches_for_the_route_table_any_more(self):
        source = Path(discover.__file__).read_text(encoding="utf-8")
        for banned in ("SOCK_DGRAM", "getsockname", "gethostname", "getaddrinfo", "_routed_address"):
            self.assertNotIn(banned, source)


class SweepRefusesTunnelsAndDeadNetworksTest(unittest.TestCase):
    """Two refusals that matter more than any hit: never sweep a tunnel, and say when the LAN is gone."""

    def use(self, rows):
        self.addCleanup(setattr, netiface, "interfaces", netiface.interfaces)
        netiface.interfaces = lambda: rows

    def test_an_explicit_subnet_that_belongs_to_a_tunnel_is_refused(self):
        """Not even on request. The range belongs to the far end of the VPN, not to this network."""
        self.use([{"name": "tun0", "address": "10.8.0.6", "prefixlen": 24, "medium": ""}])
        with self.assertRaises(ValueError) as e:
            discover.targets_for("10.8.0.0/24")
        said = str(e.exception)
        self.assertIn("tunnel", said)
        self.assertIn("not a LAN", said)

    def test_a_link_local_subnet_is_refused_however_it_is_asked_for(self):
        for cidr in ("169.254.0.0/16", "169.254.7.0/24", "169.254.7.5/32"):
            with self.assertRaises(ValueError) as e:
                discover.targets_for(cidr)
            self.assertIn("DHCP", str(e.exception))

    def test_a_sweep_where_every_address_is_unreachable_says_the_network_is_down(self):
        """The machine with its cable out: every probe fails at once, and "no miner found" would be
        the wrong answer -- it would send the owner looking at a miner that is probably fine."""
        self.addCleanup(setattr, discover, "_probe", discover._probe)
        discover._probe = lambda addr, timeout=None: ("down", None)
        with self.assertRaises(discover.NetworkDown) as e:
            discover.sweep(["192.168.50.%d" % i for i in range(1, 20)], timeout=0.1)
        said = str(e.exception)
        self.assertIn("not reachable", said)
        self.assertIn("19 addresses", said)

    def test_one_unreachable_address_among_misses_is_just_a_miss(self):
        """A single dead host is ordinary. Only a network that is entirely absent is an error."""
        answers = {"192.168.50.1": ("down", None)}
        self.addCleanup(setattr, discover, "_probe", discover._probe)
        discover._probe = lambda addr, timeout=None: answers.get(addr, ("miss", None))
        self.assertEqual(discover.sweep(["192.168.50.1", "192.168.50.2"], timeout=0.1), [])

    def test_a_hit_beside_unreachable_addresses_is_still_returned(self):
        hit = {"address": "192.168.50.5", "model": "Goldshell-SCBox"}
        self.addCleanup(setattr, discover, "_probe", discover._probe)
        discover._probe = lambda addr, timeout=None: ("hit", hit) if addr.endswith(".5") else ("down", None)
        self.assertEqual(discover.sweep(["192.168.50.4", "192.168.50.5"], timeout=0.1), [hit])

    def test_the_network_down_error_is_a_value_error_so_the_cli_exits_cleanly(self):
        self.assertTrue(issubclass(discover.NetworkDown, ValueError))


if __name__ == "__main__":
    unittest.main()
