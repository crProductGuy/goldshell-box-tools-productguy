"""The per-model rated figures behind the "% of rated" axes."""
import json
import os
import shutil
import subprocess
import unittest

from gbox import models

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = shutil.which("node")


class RatedTest(unittest.TestCase):
    def test_the_box_by_its_exact_status_string(self):
        r = models.rated_for("Goldshell-SCBox")
        self.assertEqual(r["name"], "SC-BOX")
        self.assertEqual(r["rated_mhs"], 900000.0)
        self.assertEqual(r["rated_watts"], 200.0)
        self.assertEqual(r["fan_max_rpm"], 4900.0)
        self.assertTrue(r["verified_string"])

    def test_lookup_ignores_case_spaces_and_hyphens(self):
        for s in ("goldshell-scbox", " Goldshell SCBox ", "GOLDSHELL_SCBOX", "Goldshell-SCBoxII", "goldshell scbox ii", "Goldshell-SC-Lite"):
            self.assertIsNotNone(models.rated_for(s), s)
        self.assertEqual(models.rated_for("Goldshell-SCBoxII")["name"], "SC-BOX II")
        self.assertEqual(models.rated_for("goldshell-sclite")["rated_watts"], 950.0)

    def test_unknown_model_or_none_gives_none(self):
        self.assertIsNone(models.rated_for("Goldshell-KDBox"))
        self.assertIsNone(models.rated_for(""))
        self.assertIsNone(models.rated_for(None))

    def test_pct_of(self):
        self.assertAlmostEqual(models.pct_of(737000.0, 900000.0), 81.888, places=2)
        self.assertAlmostEqual(models.pct_of(187.0, 200.0), 93.5)
        self.assertIsNone(models.pct_of(None, 200.0))
        self.assertIsNone(models.pct_of(187.0, None))
        self.assertIsNone(models.pct_of(187.0, 0))

    def test_every_row_has_the_same_keys_and_a_source(self):
        keys = {"name", "rated_mhs", "rated_watts", "fans", "fan_max_rpm", "boards", "source", "verified_string"}
        for k, v in models.MODELS.items():
            self.assertEqual(set(v), keys, k)
            self.assertTrue(v["source"], k)
            self.assertGreater(v["rated_mhs"], 0, k)
            self.assertGreater(v["rated_watts"], 0, k)

    @unittest.skipUnless(NODE, "node is not installed; skipping the app.js mirror check")
    def test_the_js_table_is_the_same_table(self):
        script = "console.log(JSON.stringify(require(%r).MODELS))" % os.path.join(HERE, "..", "gbox", "web", "app.js").replace("\\", "/")
        p = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        js = json.loads(p.stdout)
        self.assertEqual(js, json.loads(json.dumps(models.MODELS)))


if __name__ == "__main__":
    unittest.main()
