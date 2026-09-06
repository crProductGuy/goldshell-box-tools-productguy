"""The serialized session against the fake miner."""
import threading
import unittest

from gbox import api
from tests.fake_miner import FakeMiner, TOKEN


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        api.Miner.RETRY_DELAY = 0.01   # keep the retry path fast in tests

    def miner(self, **kw):
        kw.setdefault("password", "password")
        return api.Miner(self.fm.address, **kw)

    def test_login_and_typed_reads(self):
        m = self.miner()
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(m.minerinfo()["clock"], 600.0)
        self.assertEqual(len(m.boards()[0]), 16)
        self.assertEqual(len(m.history()), 288)
        self.assertEqual(self.fm.logins, 1)

    def test_wrong_password(self):
        with self.assertRaises(api.AuthError):
            self.miner(password="nope").status()

    def test_password_hex_and_token_are_alternatives(self):
        self.assertEqual(self.miner(password=None, password_hex=self.fm.password_hex).status()["firmware"], "2.2.5")
        self.assertEqual(self.miner(password=None, token=TOKEN).status()["firmware"], "2.2.5")
        self.assertEqual(self.fm.logins, 1)

    def test_no_credentials(self):
        m = api.Miner(self.fm.address)
        self.assertFalse(m.has_credentials)
        with self.assertRaises(api.NoCredentials):
            m.status()

    def test_bad_token_rejected_locally(self):
        with self.assertRaises(ValueError):
            api.Miner(self.fm.address).set_token("not a jwt")

    def test_single_401_is_retried_not_believed(self):
        m = self.miner()
        m.status()
        self.fm.unauthorized_next = 1
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(m.unauthorized_count, 1)
        self.assertEqual(self.fm.logins, 1)

    def test_persistent_401_relogs_in_once_then_fails(self):
        m = self.miner()
        m.status()
        self.fm.unauthorized_next = 3           # exhausts the retries, then re-login succeeds
        self.assertEqual(m.status()["model"], "Goldshell-SCBox")
        self.assertEqual(self.fm.logins, 2)
        self.fm.unauthorized_next = 100         # nothing helps
        with self.assertRaises(api.AuthError):
            m.status()
        self.assertFalse(m.has_token)

    def test_token_only_session_cannot_relogin(self):
        m = self.miner(password=None, token=TOKEN)
        self.fm.unauthorized_next = 3
        with self.assertRaises(api.AuthError):
            m.status()
        self.assertEqual(self.fm.logins, 0)

    def test_requests_are_serialized(self):
        self.fm.delay = 0.05
        m = self.miner()
        m.status()
        errors = []

        def worker():
            try:
                for _ in range(3):
                    m.minerinfo()
            except Exception as e:      # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.fm.max_in_flight, 1)
        self.assertEqual(m.request_count, 1 + 12)

    def test_set_plan_keeps_volts_and_fans(self):
        m = self.miner()
        self.assertEqual(m.set_plan(625), "625 MHz 0.41 V 90 RPM 90 RPM")
        self.assertTrue(self.fm.setting["manual"])
        self.assertEqual(m.set_plan(600, volts=0.4), "600 MHz 0.4 V 90 RPM 90 RPM")

    def test_set_plan_validates(self):
        m = self.miner()
        for bad in (610, 275, 750):
            with self.assertRaises(ValueError):
                m.set_plan(bad)
        self.assertEqual(self.fm.setting["manualPowerplan"], "600 MHz 0.41 V 90 RPM 90 RPM")

    def test_fan_target_clamped_to_firmware_range(self):
        m = self.miner()
        self.assertEqual(m.set_fan_target(70), 70)
        with self.assertRaises(ValueError):
            m.set_fan_target(60)

    def test_restart(self):
        self.miner().restart()
        self.assertEqual(self.fm.restarts, 1)

    def test_unreachable_is_miner_error_without_url(self):
        m = api.Miner("127.0.0.1:1", password="password", timeout=1)
        with self.assertRaises(api.MinerError) as cm:
            m.status()
        self.assertNotIn("http://", str(cm.exception))
        self.assertNotIn(self.fm.password_hex, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
