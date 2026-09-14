"""The per-model table: rated figures behind the "% of rated" axes, and the capability profile behind the seam."""
import json
import os
import shutil
import subprocess
import unittest

from gbox import models

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = shutil.which("node")

ROW_KEYS = {"name", "rated_mhs", "rated_watts", "fans", "fan_max_rpm", "boards", "source", "verified_string",
            "plan_dialect", "board_source", "dbg_expected", "fan_target", "temp_target_basis"}


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
        self.assertIsNone(models.rated_for(123))            # a hostile or odd firmware answer must not crash the health endpoint
        self.assertIsNone(models.rated_for({"model": "x"}))

    def test_pct_of(self):
        self.assertAlmostEqual(models.pct_of(737000.0, 900000.0), 81.888, places=2)
        self.assertAlmostEqual(models.pct_of(187.0, 200.0), 93.5)
        self.assertIsNone(models.pct_of(None, 200.0))
        self.assertIsNone(models.pct_of(187.0, None))
        self.assertIsNone(models.pct_of(187.0, 0))

    def test_every_row_has_the_same_keys_and_a_source(self):
        for k, v in models.MODELS.items():
            self.assertEqual(set(v), ROW_KEYS, k)
            self.assertTrue(v["source"], k)
            self.assertGreater(v["rated_mhs"], 0, k)
            self.assertGreater(v["rated_watts"], 0, k)
            self.assertIn(v["plan_dialect"], models.PLAN_DIALECTS, k)
            self.assertIn(v["board_source"], ("icinfo", "devs"), k)
            self.assertIsInstance(v["dbg_expected"], bool, k)
            self.assertIsInstance(v["fan_target"], bool, k)
            self.assertIn(v["temp_target_basis"], ("board_sensor", "fixed"), k)

    @unittest.skipUnless(NODE, "node is not installed; skipping the app.js mirror check")
    def test_the_js_table_is_the_same_table(self):
        script = "console.log(JSON.stringify(require(%r).MODELS))" % os.path.join(HERE, "..", "gbox", "web", "app.js").replace("\\", "/")
        p = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        js = json.loads(p.stdout)
        self.assertEqual(js, json.loads(json.dumps(models.MODELS)))


class ProfileTest(unittest.TestCase):
    """The capability profile: what the sampler, the plan parser and the page may assume about a unit."""

    def test_the_box_profile_is_what_the_code_assumed_until_now(self):
        p = models.profile_for("Goldshell-SCBox")
        self.assertTrue(p["known"])
        self.assertEqual(p["model"], "Goldshell-SCBox")
        self.assertEqual(p["name"], "SC-BOX")
        self.assertEqual(p["plan_dialect"], "box")
        self.assertEqual(p["board_source"], "icinfo")
        self.assertTrue(p["dbg_expected"])
        self.assertTrue(p["fan_target"])
        self.assertEqual(p["temp_target_basis"], "board_sensor")
        self.assertEqual(p["rated_watts"], 200.0)

    def test_the_sc_lite_profile_from_the_other_developers_notes(self):
        p = models.profile_for("Goldshell-SCLITE")
        self.assertTrue(p["known"])
        self.assertEqual(p["plan_dialect"], "mv_pv")
        self.assertEqual(p["board_source"], "devs")
        self.assertFalse(p["dbg_expected"])          # /dbg/ answers 401 until the stock UI's debug page is unlocked
        self.assertFalse(p["fan_target"])            # the target is a fixed 85 C, read-only
        self.assertEqual(p["temp_target_basis"], "fixed")
        self.assertFalse(p["verified_string"])

    def test_an_unknown_model_gets_the_box_path_with_every_optional_capability_off(self):
        for m in ("Goldshell-KDBox", "", None, 123):
            p = models.profile_for(m)
            self.assertFalse(p["known"], m)
            self.assertEqual(p["plan_dialect"], "box", m)
            self.assertEqual(p["board_source"], "icinfo", m)
            self.assertTrue(p["dbg_expected"], m)
            self.assertFalse(p["fan_target"], m)
            self.assertIsNone(p["rated_mhs"], m)
            self.assertIsNone(p["rated_watts"], m)
            self.assertIsNone(p["fan_max_rpm"], m)
        self.assertEqual(models.profile_for("Goldshell-KDBox")["model"], "Goldshell-KDBox")
        self.assertEqual(models.profile_for("Goldshell-KDBox")["name"], "Goldshell-KDBox")
        self.assertIsNone(models.profile_for(None)["model"])

    def test_profile_carries_the_same_keys_known_or_not(self):
        known, unknown = models.profile_for("Goldshell-SCBox"), models.profile_for("nope")
        self.assertEqual(set(known), set(unknown))
        self.assertEqual(set(known), ROW_KEYS | {"known", "model"})
        json.dumps(known); json.dumps(unknown)       # it goes out through /api/health

    def test_profile_does_not_alias_the_table(self):
        p = models.profile_for("Goldshell-SCBox")
        p["fan_target"] = False
        self.assertTrue(models.profile_for("Goldshell-SCBox")["fan_target"])

    @unittest.skipUnless(NODE, "node is not installed; skipping the app.js mirror check")
    def test_the_js_profile_agrees(self):
        script = ("const a = require(%r); console.log(JSON.stringify([a.profileFor('Goldshell-SCLITE'), a.profileFor('Goldshell-KDBox'), a.profileFor(null)]))"
                  % os.path.join(HERE, "..", "gbox", "web", "app.js").replace("\\", "/"))
        p = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        js = json.loads(p.stdout)
        py = [models.profile_for("Goldshell-SCLITE"), models.profile_for("Goldshell-KDBox"), models.profile_for(None)]
        self.assertEqual(js, json.loads(json.dumps(py)))


if __name__ == "__main__":
    unittest.main()
