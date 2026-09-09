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


if __name__ == "__main__":
    unittest.main()
