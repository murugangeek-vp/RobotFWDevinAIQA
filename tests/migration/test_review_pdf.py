import tempfile
import unittest
from unittest.mock import MagicMock

from libs.reporting.review_pdf import build_review, write_review_pdf


def _summary(verdict="PASS", coverage="COMPLETE"):
    return {
        "verdict": verdict,
        "coverage": coverage,
        "meta": {
            "environment": "env",
            "hop": "hop1",
            "selected": ["m1"],
            "started": "2026-01-01T00:00:00",
            "snapshot_id": "2026-01-01",
        },
        "results": [],
    }


def _outcome(name, status, checks=None):
    return {"test": f"Suite.{name}", "status": status, "checks": checks or []}


class BuildReviewTests(unittest.TestCase):
    def test_pass_run(self):
        review = build_review(_summary(), test_outcomes=[_outcome("T One", "PASS")])
        self.assertEqual(review["status"], "PASS")
        self.assertEqual(review["cases"][0]["name"], "T One")
        self.assertEqual(review["cases"][0]["case_id"], "TC-01")

    def test_failed_test_means_fail(self):
        review = build_review(
            _summary(),
            test_outcomes=[
                _outcome(
                    "T",
                    "FAIL",
                    [{"stage": "recon", "status": "FAIL", "mapping": "m", "detail": "1 missing"}],
                )
            ],
        )
        self.assertEqual(review["status"], "FAIL")
        self.assertIn("re-run", review["action"])

    def test_skipped_test_means_incomplete(self):
        review = build_review(_summary(), test_outcomes=[_outcome("T", "SKIP")])
        self.assertEqual(review["status"], "INCOMPLETE")
        self.assertEqual(review["skipped"], ["T"])

    def test_partial_coverage_means_incomplete(self):
        review = build_review(_summary(coverage="PARTIAL"))
        self.assertEqual(review["status"], "INCOMPLETE")

    def test_not_run(self):
        review = build_review({"verdict": "NOT_RUN", "meta": {}, "results": []})
        self.assertEqual(review["status"], "NOT RUN")

    def test_fail_verdict_means_fail(self):
        review = build_review(_summary(verdict="FAIL"), test_outcomes=[_outcome("T", "PASS")])
        self.assertEqual(review["status"], "FAIL")

    def test_rule_error_means_fail(self):
        rule = MagicMock(test_id="R1", rule_type="NULL_CHECK", severity="HIGH")
        result = MagicMock(rule=rule, status="ERROR", violations=[], error="boom")
        review = build_review(
            _summary(), rule_results=[result], test_outcomes=[_outcome("T", "PASS")]
        )
        self.assertEqual(review["status"], "FAIL")
        self.assertEqual(review["rule_rows"][0]["test_id"], "R1")


class WritePdfTests(unittest.TestCase):
    def test_writes_valid_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_review_pdf(tmp, _summary(), test_outcomes=[_outcome("T", "PASS")])
            self.assertTrue(path.exists())
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            self.assertGreater(path.stat().st_size, 1000)

    def test_writes_pdf_with_failures(self):
        checks = [{"stage": "recon", "status": "FAIL", "mapping": "m", "detail": "5 extra"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_review_pdf(
                tmp, _summary(verdict="FAIL"), test_outcomes=[_outcome("T", "FAIL", checks)]
            )
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
