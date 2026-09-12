"""The dashboard's data layer (gbox/web/app.js) under Node, when Node is installed.

app.js exports its pure functions through a `module.exports` guard and keeps
all DOM code behind a `typeof document` check, so Node can load it. The
assertions live in tests/app_test.js; this wrapper makes them part of
`python -m unittest` and skips cleanly on a machine without Node.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed; skipping the app.js unit tests")
class AppJsTest(unittest.TestCase):
    def test_request_builders(self):
        p = subprocess.run([NODE, os.path.join(HERE, "app_test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, "\n" + p.stdout + p.stderr)
        self.assertIn("passed", p.stdout)


class VersionMirrorTest(unittest.TestCase):
    def test_app_js_carries_the_package_version(self):
        """The page shows its own version (it may be opened as a file, with no service to ask); keep it equal."""
        import re
        from gbox import __version__
        with open(os.path.join(HERE, "..", "gbox", "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        m = re.search(r'^const VERSION = "([^"]+)";', js, re.M)
        self.assertIsNotNone(m, "app.js has no `const VERSION = \"...\";` line")
        self.assertEqual(m.group(1), __version__)


if __name__ == "__main__":
    unittest.main()
