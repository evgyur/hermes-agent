import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.seo_scorecard import compute_scorecard, rank_priorities
from scripts.measurement_adapters import MockMeasurementAdapter, merge_measurement
from scripts.report_generator import render_markdown

ROOT = Path(__file__).resolve().parents[1]


class PrincipalPlusTests(unittest.TestCase):
    def load_fixture(self, name):
        return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))

    def test_scorecard_rows_have_principal_plus_fields(self):
        score = compute_scorecard(self.load_fixture("clean-site.json"))
        self.assertEqual(score["model_version"], "2.0-principal-plus")
        for row in score["scorecard"]:
            for key in ["evidence_tier", "confidence", "impact", "effort", "priority", "residual_gap"]:
                self.assertIn(key, row)

    def test_performance_stays_low_confidence_without_measurement(self):
        score = compute_scorecard(self.load_fixture("clean-site.json"))
        perf = [r for r in score["scorecard"] if r["criterion"] == "Performance confidence"][0]
        self.assertEqual(perf["score"], 6)
        self.assertEqual(perf["evidence_tier"], "assumption")
        self.assertEqual(perf["confidence"], "low")

    def test_measurement_adapter_can_raise_performance_with_evidence(self):
        audit = self.load_fixture("clean-site.json")
        merged = merge_measurement(audit, MockMeasurementAdapter("lighthouse", 0.91).fetch("https://fixture.test"))
        score = compute_scorecard(merged)
        perf = [r for r in score["scorecard"] if r["criterion"] == "Performance confidence"][0]
        self.assertGreaterEqual(perf["score"], 9)
        self.assertEqual(perf["evidence_tier"], "external_current_source")

    def test_fixture_archetypes_trigger_different_priorities(self):
        cases = {
            "thin-content-site.json": "Content depth",
            "schema-missing-site.json": "Structured data coverage",
            "discovery-weak-site.json": "GEO / agent discovery",
            "image-alt-debt-site.json": "Image SEO/accessibility",
        }
        for fixture, expected_top in cases.items():
            score = compute_scorecard(self.load_fixture(fixture))
            priorities = rank_priorities(score["scorecard"])
            names = [r["criterion"] for r in priorities[:3]]
            self.assertIn(expected_top, names)

    def test_report_contains_evidence_tiers_and_residual_gaps(self):
        md = render_markdown(self.load_fixture("schema-missing-site.json"), title="Fixture")
        self.assertIn("Evidence tier", md)
        self.assertIn("Residual measurement gaps", md)
        self.assertIn("Structured data coverage", md)

    def test_cli_fixture_report_smoke(self):
        fixture = ROOT / "fixtures" / "clean-site.json"
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "seo_audit_cli.py"), "fixture-report", "--fixture", str(fixture)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Score:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
