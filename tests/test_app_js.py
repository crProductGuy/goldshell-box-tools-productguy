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


if __name__ == "__main__":
    unittest.main()
