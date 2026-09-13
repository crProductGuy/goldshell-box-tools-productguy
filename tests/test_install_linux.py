"""scripts/install-linux.sh in dry-run mode: the unit it would write and the commands it would run.

Runs under any bash (Git Bash on Windows included); systemd itself is never
called in dry-run, so the test is real on a machine without it. The live
check (fresh Ubuntu, three commands, dashboard up, survives logout and
reboot) is the checklist in the script's header, run on a Linux box.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "install-linux.sh")
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is not installed; skipping the Linux installer dry-run tests")
class InstallLinuxDryRunTest(unittest.TestCase):
    def run_script(self, *args):
        p = subprocess.run([BASH, SCRIPT, "--dry-run", "--python", "/usr/bin/python3"] + list(args),
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME="/home/tester"))
        return p.returncode, p.stdout + p.stderr

    def test_install_writes_a_user_unit_and_enables_it(self):
        code, out = self.run_script("--data", "/srv/gbox-data")
        self.assertEqual(code, 0, out)
        self.assertIn("[Unit]", out)
        self.assertIn("ExecStart=/usr/bin/python3 -m gbox serve --data /srv/gbox-data", out)
        self.assertIn("WorkingDirectory=", out)
        self.assertIn("Restart=on-failure", out)
        self.assertIn("WantedBy=default.target", out)
        self.assertIn("/home/tester/.config/systemd/user/gbox.service", out)
        for cmd in ("systemctl --user daemon-reload", "systemctl --user enable --now gbox.service", "loginctl enable-linger"):
            self.assertIn(cmd, out)
        self.assertIn("dry run", out.lower())

    def test_install_without_data_uses_the_default_directory(self):
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn("ExecStart=/usr/bin/python3 -m gbox serve\n", out)
        self.assertNotIn("--data", out.split("ExecStart=")[1].split("\n")[0])

    def test_uninstall_disables_and_removes_the_unit(self):
        code, out = self.run_script("--uninstall")
        self.assertEqual(code, 0, out)
        self.assertIn("systemctl --user disable --now gbox.service", out)
        self.assertIn("rm -f /home/tester/.config/systemd/user/gbox.service", out)
        self.assertIn("systemctl --user daemon-reload", out)
        self.assertNotIn("enable-linger", out)

    def test_unknown_flag_is_refused(self):
        code, out = self.run_script("--explode")
        self.assertEqual(code, 2)
        self.assertIn("usage", out.lower())

    def test_script_parses(self):
        p = subprocess.run([BASH, "-n", SCRIPT], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
