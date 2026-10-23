"""Unit tests for the consolidated end-to-end lifecycle report merger."""

import tempfile
import unittest
from pathlib import Path

import openpyxl

from libs.excelrules.models import Rule, RuleResult, Violation
from libs.reporting import consolidated_report


def _run(verdict="PASS"):
    return {
        "verdict": verdict,
        "coverage": "COMPLETE",
        "cutover": {
            "snapshot_id": "2026-10-15",
            "source_snapshot": "00000003-00000164-1",
            "snapshot_enforced": True,
        },
        "results": [
            {
                "mapping": "csv_cup00301",
                "stage": "recon",
                "status": "PASS",
                "detail": "200 source rows, 200 target rows",
                "source_count": 200,
                "target_count": 200,
                "seconds": 1.2,
            },
            {
                "mapping": "csv_cup00301",
                "stage": "controls",
                "status": "FAIL" if verdict == "FAIL" else "PASS",
                "detail": "8 control(s) certified",
                "control_mismatches": (
                    [{"control": "naics_sum", "group": "", "expected": "1", "actual": "2"}]
                    if verdict == "FAIL"
                    else []
                ),
            },
        ],
    }


def _results(status_violations):
    rule = Rule(
        test_id="BR-AGG-UNIQ-CUNBR",
        rule_type="CUSTOM_SQL",
        target_table="cup00301",
        target_column="cunbr",
        severity="CRITICAL",
        key_column="cunbr",
        business_rule="customer number must be unique",
    )
    return [
        RuleResult(
            rule=rule,
            violations=status_violations,
            sql="SELECT cunbr FROM signature.cup00301 WHERE ...",
        )
    ]


class ConsolidatedReportTest(unittest.TestCase):
    def _write(self, verdict="PASS", violations=None):
        tmp = tempfile.mkdtemp()
        path = consolidated_report.write_consolidated(
            _run(verdict),
            _results(violations or []),
            tmp,
            meta={"environment": "phase1-hop1", "timestamp": "2026-10-15T00:00:00"},
        )
        return Path(tmp), path

    def test_writes_workbook_and_markdown(self):
        tmp, path = self._write()
        self.assertTrue(path.exists())
        self.assertTrue((tmp / "end_to_end_report.md").exists())
        wb = openpyxl.load_workbook(path)
        self.assertEqual(
            sorted(wb.sheetnames),
            [
                "Column Profile",
                "Controls",
                "Data Diffs",
                "Insights",
                "Metadata",
                "Queries",
                "Rules",
                "Run Info",
                "Stages",
                "Violations",
            ],
        )

    def test_overall_status_reflects_failures(self):
        tmp, path = self._write(verdict="FAIL")
        wb = openpyxl.load_workbook(path)
        vals = {r[0].value: r[1].value for r in wb["Run Info"].iter_rows(min_row=2)}
        self.assertEqual(vals["Overall Status"], "FAIL")
        self.assertEqual(vals["Verdict"], "FAIL")
        # control mismatch is flattened onto the Controls sheet
        rows = list(wb["Controls"].iter_rows(min_row=2, values_only=True))
        self.assertIn(("csv_cup00301", "naics_sum", None, "1", "2"), rows)

    def test_rules_sheet_carries_plain_english(self):
        tmp, path = self._write()
        wb = openpyxl.load_workbook(path)
        rows = list(wb["Rules"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(rows[0][0], "BR-AGG-UNIQ-CUNBR")
        self.assertEqual(rows[0][2], "customer number must be unique")
        self.assertEqual(rows[0][4], "PASS")

    def test_violations_flattened(self):
        viol = [Violation("BR-1", "cunbr", "expected-9-digit", "12345", key="C000001")]
        tmp, path = self._write(violations=viol)
        wb = openpyxl.load_workbook(path)
        rows = list(wb["Violations"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(rows[0][0], "BR-AGG-UNIQ-CUNBR")
        self.assertEqual(rows[0][4], "expected-9-digit")
        md = (tmp / "end_to_end_report.md").read_text(encoding="utf-8")
        self.assertIn("BR-AGG-UNIQ-CUNBR", md)


if __name__ == "__main__":
    unittest.main()
