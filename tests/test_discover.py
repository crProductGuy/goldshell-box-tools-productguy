"""The LAN sweep: one tokenless GET /mcb/status per address, against fakes only.

No test here touches the real LAN. `sweep` is given an explicit target list in
every case, and `targets_for` is exercised on its arithmetic, never probed.
"""
import ipaddress
import unittest

from gbox import discover
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


if __name__ == "__main__":
    unittest.main()
