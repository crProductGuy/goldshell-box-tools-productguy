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


if __name__ == "__main__":
    unittest.main()
