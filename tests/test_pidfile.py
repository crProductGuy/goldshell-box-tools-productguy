"""The pid file that lets a running service be identified precisely.

It exists because two services running `pythonw -m gbox serve` have identical command lines, so a
filter on the command line cannot tell the owner's live service from a scratch one -- which is how
an agent session stopped the live service on 2026-09-19 while cleaning up after a test.
"""
import json
import os
import tempfile
import unittest

from gbox import pidfile


class PidFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_then_read_round_trips_and_names_this_process(self):
        written = pidfile.write(self.dir, 8765, "0.7.4")
        self.assertEqual(written, os.path.join(self.dir, "gbox.pid"))
        d = pidfile.read(self.dir)
        self.assertEqual((d["pid"], d["port"], d["version"]), (os.getpid(), 8765, "0.7.4"))
        self.assertRegex(d["started"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_running_reports_this_process_and_not_a_dead_one(self):
        pidfile.write(self.dir, 8765, "0.7.4")
        self.assertIsNotNone(pidfile.running(self.dir))          # this very process is alive
        # A file left behind by a process that has since died must read as not running, or a caller
        # would go looking for a service that is not there.
        with open(pidfile.path(self.dir), "w") as f:
            json.dump({"pid": 999_999_998, "port": 8765, "version": "0.7.4", "started": "x"}, f)
        self.assertIsNone(pidfile.running(self.dir))
        self.assertIsNotNone(pidfile.read(self.dir))             # read() still reports what it says

    def test_two_data_dirs_keep_separate_records(self):
        """The whole point: a scratch service and the live one are distinguishable."""
        with tempfile.TemporaryDirectory() as other:
            pidfile.write(self.dir, 8765, "0.7.4")
            pidfile.write(other, 8770, "0.7.4")
            self.assertEqual(pidfile.read(self.dir)["port"], 8765)
            self.assertEqual(pidfile.read(other)["port"], 8770)

    def test_remove_only_takes_its_own_record(self):
        with open(pidfile.path(self.dir), "w") as f:
            json.dump({"pid": os.getpid() + 1, "port": 1, "version": "v", "started": "x"}, f)
        self.assertFalse(pidfile.remove(self.dir))               # someone else's: left alone
        self.assertIsNotNone(pidfile.read(self.dir))
        pidfile.write(self.dir, 8765, "0.7.4")
        self.assertTrue(pidfile.remove(self.dir))
        self.assertIsNone(pidfile.read(self.dir))

    def test_every_call_is_best_effort(self):
        """A service must never fail to start, or fail to stop, because of this file."""
        missing = os.path.join(self.dir, "no", "such", "dir")
        self.assertIsNone(pidfile.write(missing, 8765, "0.7.4"))
        self.assertIsNone(pidfile.read(missing))
        self.assertIsNone(pidfile.running(missing))
        self.assertFalse(pidfile.remove(missing))
        with open(pidfile.path(self.dir), "w") as f:
            f.write("{not json")
        self.assertIsNone(pidfile.read(self.dir))
        self.assertIsNone(pidfile.running(self.dir))


if __name__ == "__main__":
    unittest.main()


class ServeRefusesASharedDataDirTest(unittest.TestCase):
    """0.13.0: a second `gbox serve` on a data directory a live service already uses refuses to start, instead
    of overwriting its gbox.pid and writing into the same log.csv (review b-i 5, ahead of more miners)."""

    def test_second_service_on_the_same_data_dir_exits(self):
        import subprocess
        import sys
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(other.kill)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump({"host": "127.0.0.1:1"}, f)
            with open(pidfile.path(d), "w") as f:
                json.dump({"pid": other.pid, "port": 8765, "version": "x", "started": "x"}, f)
            env = dict(os.environ, GBOX_DATA=d)
            r = subprocess.run([sys.executable, "-m", "gbox", "serve", "--port", "18999"], env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already uses", r.stderr)
            with open(pidfile.path(d)) as f:
                self.assertEqual(json.load(f)["pid"], other.pid)      # the live record is untouched
