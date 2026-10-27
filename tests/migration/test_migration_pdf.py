import tempfile
import unittest
from unittest.mock import MagicMock

from libs.reporting.migration_pdf import REPORT_NAME, build_report, write_migration_pdf


def _summary(verdict="PASS", coverage="COMPLETE", results=None):
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
        "results": results or [],
    }


def _outcome(name, status, checks=None):
    return {"test": f"Suite.{name}", "status": status, "checks": checks or []}


def _failed_recon():
    return {
        "mapping": "m1",
        "stage": "recon",
        "status": "FAIL",
        "detail": "0 missing / 5 extra / 0 column diffs",
        "extra_samples": ["k1", "k2", "k3"],
    }


class BuildReportTests(unittest.TestCase):
    def test_pass_run(self):
        report = build_report(_summary(), test_outcomes=[_outcome("T One", "PASS")])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["cases"][0]["name"], "T One")
        self.assertEqual(report["cases"][0]["case_id"], "TC-01")

    def test_failed_test_means_fail(self):
        report = build_report(_summary(), test_outcomes=[_outcome("T", "FAIL")])
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("re-run", report["action"])

    def test_skipped_test_means_incomplete(self):
        report = build_report(_summary(), test_outcomes=[_outcome("T", "SKIP")])
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["skipped"], ["T"])

    def test_partial_coverage_means_incomplete(self):
        report = build_report(_summary(coverage="PARTIAL"))
        self.assertEqual(report["status"], "INCOMPLETE")

    def test_not_run(self):
        report = build_report({"verdict": "NOT_RUN", "meta": {}, "results": []})
        self.assertEqual(report["status"], "NOT RUN")

    def test_fail_verdict_means_fail(self):
        report = build_report(_summary(verdict="FAIL"), test_outcomes=[_outcome("T", "PASS")])
        self.assertEqual(report["status"], "FAIL")

    def test_rule_error_means_fail(self):
        rule = MagicMock(test_id="R1", rule_type="NULL_CHECK", severity="HIGH")
        result = MagicMock(rule=rule, status="ERROR", violations=[], error="boom")
        report = build_report(
            _summary(), rule_results=[result], test_outcomes=[_outcome("T", "PASS")]
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["rule_rows"][0]["test_id"], "R1")

    def test_stage_rows_and_failures_with_proof(self):
        report = build_report(_summary(verdict="FAIL", results=[_failed_recon()]))
        self.assertEqual(len(report["stage_rows"]), 1)
        self.assertEqual(report["stage_rows"][0]["status"], "FAIL")
        self.assertEqual(len(report["failures"]), 1)
        self.assertIn("5 extra", report["failures"][0]["reason"])
        self.assertIn("unexpected key k1", report["failures"][0]["proof"][0])

    def test_failures_include_full_problem_records(self):
        row = _failed_recon()
        row["record_samples"] = {
            "k1": {
                "source": {"id": "k1", "name": "J***s", "code": "826"},
                "target": None,
            }
        }
        report = build_report(_summary(verdict="FAIL", results=[row]))
        records = report["failures"][0]["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], "k1")
        self.assertEqual(records[0][1]["source"]["code"], "826")
        self.assertIsNone(records[0][1]["target"])
        self.assertEqual(report["failures"][0]["records_total"], 1)

    def test_problem_record_columns_filtered(self):
        row = _failed_recon()
        row["record_samples"] = {
            "k1": {
                "source": {"id": "k1", "name": "J***s", "load_date": "2026-01-01", "extra": "x"},
                "target": {"id": "k1", "name": "m***n", "load_date": "2026-01-01", "extra": "x"},
            }
        }
        summary = _summary(verdict="FAIL", results=[row])
        summary["meta"]["mappings"] = [
            {
                "mapping_id": "m1",
                "keys": ["id"],
                "fk_columns": [],
                "fields": [
                    {"source": "id", "target": "id", "type": "string"},
                    {"source": "load_date", "target": "load_date", "type": "date"},
                ],
            }
        ]
        report = build_report(summary)
        cols = report["failures"][0]["record_columns"]
        self.assertEqual(cols, ["id", "name", "load_date"])  # key, diff, date; "extra" dropped

    def test_duplicate_stage_rows_deduped(self):
        row = _failed_recon()
        report = build_report(_summary(verdict="FAIL", results=[row, dict(row)]))
        self.assertEqual(len(report["stage_rows"]), 1)
        self.assertEqual(len(report["failures"]), 1)

    def test_file_comparison_collected_once(self):
        row = {
            "mapping": "m1",
            "stage": "negative",
            "status": "PASS",
            "file_comparison": [
                {
                    "variant": "clean baseline",
                    "source_rows": 200,
                    "negative_rows": 200,
                    "row_difference": 0,
                    "columns_match": True,
                }
            ],
        }
        report = build_report(_summary(results=[row, dict(row)]))
        fc = report["file_comparison"]
        self.assertEqual(len(fc), 1)
        self.assertEqual(fc[0]["mapping"], "m1")
        self.assertEqual(fc[0]["source_rows"], 200)
        self.assertEqual(fc[0]["row_difference"], 0)


class WritePdfTests(unittest.TestCase):
    def test_writes_valid_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_migration_pdf(tmp, _summary(), test_outcomes=[_outcome("T", "PASS")])
            self.assertEqual(path.name, REPORT_NAME)
            self.assertTrue(path.exists())
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            self.assertGreater(path.stat().st_size, 1000)

    def test_writes_pdf_with_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_migration_pdf(
                tmp,
                _summary(verdict="FAIL", results=[_failed_recon()]),
                test_outcomes=[_outcome("T", "FAIL")],
            )
            self.assertTrue(path.exists())

    def test_writes_pdf_with_mapping_workbook_section(self):
        summary = _summary()
        summary["meta"]["mappings"] = [
            {
                "mapping_id": "dim_customer_migration",
                "source": "signature.v_dim_customer_src",
                "target": "wdr_il_core.dim_customer",
                "keys": ["involved_party_key"],
                "enabled": "yes",
                "fields": [
                    {
                        "source": "cunbr",
                        "target": "involved_party_key",
                        "type": "string",
                        "nullable": "no",
                        "transformation": "concat('CBS', 'CUST', cubk, cunbr)",
                    },
                    {
                        "source": "load_date",
                        "target": "load_date",
                        "type": "date",
                        "nullable": "no",
                        "transformation": "Direct rename",
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_migration_pdf(tmp, summary, test_outcomes=[_outcome("T", "PASS")])
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
