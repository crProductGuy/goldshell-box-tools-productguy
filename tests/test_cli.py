"""`gbox trials`: the clock-trials table from the data directory's log, no miner contact."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from gbox import cli, poller
from tests.test_trials import fixture_rows, write_csv


class TrialsCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--data", self.tmp.name] + list(argv))
        return out.getvalue()

    def table_lines(self, *argv):
        """The table block: everything before the blank line that separates it from the notes."""
        return self.run_cli(*argv).split("\n\n", 1)[0].splitlines()

    def test_prints_rollup_rows(self):
        write_csv(self.data / "log.csv", fixture_rows(), poller.COLUMNS)
        lines = self.table_lines("trials")
        self.assertEqual(len(lines), 1 + 3)
        self.assertIn("clock", lines[0])
        self.assertIn("1 segment under 20 min hidden", self.run_cli("trials"))

    def test_segments_and_min_flags(self):
        write_csv(self.data / "log.csv", fixture_rows(), poller.COLUMNS)
        self.assertEqual(len(self.table_lines("trials", "--segments")), 1 + 4)
        self.assertEqual(len(self.table_lines("trials", "--segments", "--min", "5")), 1 + 5)

    def test_no_log_says_so(self):
        text = self.run_cli("trials")
        self.assertIn("no samples", text)


class TrialsRunCommandTest(unittest.TestCase):
    """`gbox trials run` against the fake miner and a real service on a scratch port."""

    def setUp(self):
        import os
        import threading
        from gbox import api, config
        from gbox.events import EventLog
        from gbox.poller import Poller
        from gbox.server import ServiceState, make_server
        from tests.fake_miner import FakeMiner
        self.fm = FakeMiner().start()
        self.addCleanup(self.fm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        cfg = config.Config(host=self.fm.address, port=0)
        miner = api.Miner(self.fm.address, password="password")
        self.events = EventLog(self.data / "events.log")
        self.poller = Poller(miner, self.data / "log.csv", 30, events=self.events)
        self.srv = make_server(ServiceState(cfg, miner, self.data, poller=self.poller, events=self.events))
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        cfg.port = self.srv.server_address[1]
        config.save(cfg, self.data)
        os.environ["GBOX_PASSWORD"] = "password"
        self.addCleanup(os.environ.pop, "GBOX_PASSWORD", None)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--data", self.tmp.name] + list(argv))
        return out.getvalue()

    def test_runs_a_step_and_logs_it_through_the_service(self):
        self.poller.poll_once()                                  # a fresh sample so the log is not stale
        text = self.run_cli("trials", "run", "550", "--hours", "0.0003", "--settle", "0", "--check", "0.01", "--end", "525")
        self.assertIn("finished", text)
        events = (self.data / "events.log").read_text(encoding="utf-8")
        self.assertIn("trial: step 1/1, clock set to 550 MHz", events)
        self.assertIn("trial: finished all 1 step, clock set to 525 MHz", events)
        self.assertIn("525 MHz", self.fm.setting["manualPowerplan"])
        self.assertFalse((self.data / "trial.json").exists())

    def test_refuses_to_run_without_the_service(self):
        self.srv.shutdown(); self.srv.server_close()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_cli("trials", "run", "550", "--hours", "0.0003")
        self.assertIn("gbox serve", err.getvalue())
        self.assertIn("600 MHz", self.fm.setting["manualPowerplan"])          # nothing was sent

    def test_bad_clock_dies_before_sending(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_cli("trials", "run", "560", "--hours", "1")
        self.assertIn("multiples of 25", err.getvalue())
        self.assertIn("600 MHz", self.fm.setting["manualPowerplan"])


class FakeTTY(io.StringIO):
    """stdin that claims to be a terminal, so prompts are asked and answered from the string."""

    def isatty(self):
        return True


class PowerCommandTest(unittest.TestCase):
    """`gbox power discover | init | status | cycle` against the fake plug; the real plug is never touched here."""

    def setUp(self):
        import sys
        from gbox import config
        from tests.fake_plug import FakePlug
        self.fake = FakePlug(udp_port=0).start()
        self.addCleanup(self.fake.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        config.save(config.Config(host="miner", port=1), self.data)      # port 1: never the live service
        self._stdin = sys.stdin
        self.addCleanup(setattr, sys, "stdin", self._stdin)

    def run_cli(self, *argv, stdin=None):
        import sys
        if stdin is not None:
            sys.stdin = stdin
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--data", self.tmp.name] + list(argv))
        return out.getvalue()

    def run_cli_dies(self, *argv, stdin=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.run_cli(*argv, stdin=stdin)
        return cm.exception.code, err.getvalue()

    def power_block(self):
        from gbox import config
        return config.load(self.data).power

    def relay_commands(self):
        return [c[2]["state"] for c in self.fake.commands if c[1] == "set_relay_state"]

    def test_init_with_yes_writes_a_dry_run_block_with_the_device_id(self):
        text = self.run_cli("power", "init", "--plug", self.fake.address, "--yes")
        p = self.power_block()
        self.assertEqual(p["host"], self.fake.address)
        self.assertEqual(p["device_id"], self.fake.device_id)
        self.assertIs(p["cycle"], False)
        self.assertIn("dry run", text)
        self.assertIn("HS110(US)", text)
        self.assertIn("188 W", text)

    def test_init_asks_and_a_no_writes_nothing(self):
        self.run_cli_dies("power", "init", "--plug", self.fake.address, stdin=FakeTTY("n\n"))
        self.assertIsNone(self.power_block())

    def test_init_asks_and_a_yes_writes_the_block(self):
        self.run_cli("power", "init", "--plug", self.fake.address, stdin=FakeTTY("y\n"))
        self.assertEqual(self.power_block()["device_id"], self.fake.device_id)

    def test_init_without_a_terminal_needs_yes(self):
        code, err = self.run_cli_dies("power", "init", "--plug", self.fake.address, stdin=io.StringIO(""))
        self.assertIn("--yes", err)
        self.assertIsNone(self.power_block())

    def test_init_against_a_dead_address_exits_2(self):
        code, err = self.run_cli_dies("power", "init", "--plug", "127.0.0.1:1", "--yes")
        self.assertEqual(code, 2)
        self.assertIsNone(self.power_block())

    def test_status_shows_relay_watts_and_dry_run(self):
        self.run_cli("power", "init", "--plug", self.fake.address, "--yes")
        text = self.run_cli("power", "status")
        self.assertIn("on", text)
        self.assertIn("188 W", text)
        self.assertIn("dry run", text)
        self.assertIn("service not running", text)

    def test_status_without_init_says_so(self):
        code, err = self.run_cli_dies("power", "status")
        self.assertIn("gbox power init", err)

    def test_cycle_refuses_without_the_word_and_sends_nothing(self):
        self.run_cli("power", "init", "--plug", self.fake.address, "--yes")
        code, err = self.run_cli_dies("power", "cycle", stdin=FakeTTY("yes\n"))
        self.assertEqual(code, 1)
        self.assertEqual(self.relay_commands(), [])
        self.assertEqual(self.fake.relay, 1)

    def test_cycle_with_the_word_turns_off_then_on(self):
        self.run_cli("power", "init", "--plug", self.fake.address, "--yes")
        text = self.run_cli("power", "cycle", "--off-seconds", "3", stdin=FakeTTY("CYCLE\n"))
        self.assertEqual(self.relay_commands(), [0, 1])
        self.assertEqual(self.fake.relay, 1)
        self.assertIn("cycled", text)
        self.assertIn("back hashing in about a minute", text)  # observed 60 to 66 s on the SC-BOX, not "2-3 minutes"
        self.assertNotIn("2-3 minutes", text)
        self.assertIn("nothing was logged", text)              # no service running

    def test_cycle_refuses_a_plug_whose_device_id_differs(self):
        from gbox import config
        cfg = config.load(self.data)
        cfg.power = {"host": self.fake.address, "device_id": "some-other-plug"}
        config.save(cfg, self.data)
        code, err = self.run_cli_dies("power", "cycle", stdin=FakeTTY("CYCLE\n"))
        self.assertEqual(code, 1)
        self.assertIn("device id", err)
        self.assertEqual(self.relay_commands(), [])

    def test_cycle_when_the_plug_does_not_answer_exits_2(self):
        self.run_cli("power", "init", "--plug", self.fake.address, "--yes")
        self.fake.hang = True
        code, err = self.run_cli_dies("power", "cycle", stdin=FakeTTY("CYCLE\n"))
        self.assertEqual(code, 2)

    def test_discover_lists_the_fake_plug(self):
        text = self.run_cli("power", "discover", "--timeout", "0.5", "--port", str(self.fake.udp_port), "--target", "127.0.0.1")
        self.assertIn("HS110(US)", text)
        self.assertIn("127.0.0.1", text)
        self.assertIn("188", text)
        self.assertIn("legacy", text)

    def test_discover_with_nothing_answering_says_none_found(self):
        text = self.run_cli("power", "discover", "--timeout", "0.3", "--port", "1", "--target", "127.0.0.1")
        self.assertIn("none found", text)


class PowerCycleEventTest(TrialsRunCommandTest):
    """`gbox power cycle` writes its event line through a running service."""

    def test_cycle_by_hand_is_logged_through_the_service(self):
        import sys
        from gbox import config
        from tests.fake_plug import FakePlug
        fake = FakePlug().start()
        self.addCleanup(fake.stop)
        cfg = config.load(self.data)
        cfg.power = {"host": fake.address, "device_id": fake.device_id}
        config.save(cfg, self.data)
        old = sys.stdin
        sys.stdin = FakeTTY("CYCLE\n")
        self.addCleanup(setattr, sys, "stdin", old)
        text = self.run_cli("power", "cycle", "--off-seconds", "3")
        self.assertIn("cycled", text)
        events = (self.data / "events.log").read_text(encoding="utf-8")
        self.assertIn("power: cycled by hand", events)
        self.assertIn("188 W before", events)

    # the inherited trials tests would run again under this class; hide them
    test_runs_a_step_and_logs_it_through_the_service = None
    test_refuses_to_run_without_the_service = None
    test_bad_clock_dies_before_sending = None


if __name__ == "__main__":
    unittest.main()
