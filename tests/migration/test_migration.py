import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pandas as pd
from test_streaming import contract, frames

from libs.adapters.snowflake_target import SnowflakeTarget
from libs.adapters.staging import ordered_chunks
from libs.engine.mapping_transforms import evaluate, mapped_rows, parse_expression
from libs.engine.reconcile import compare_chunked, normalize
from libs.mapping import builder, parser
from libs.reporting.report import write_evidence

ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = ROOT / "data/mapping/bank_mapping.xlsx"


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tables, self.codes = parser.load_workbook(str(WORKBOOK))

    def compile(self, table=None):
        return builder.build_contract(
            table or self.tables[0],
            self.codes,
            {"type": "postgres", "host": "localhost", "database": "synthetic"},
            {"type": "snowflake"},
        )

    def test_all_enabled_mappings_compile(self):
        enabled = [t for t in self.tables if t.enabled]
        self.assertEqual(len(enabled), 5)
        for t in enabled:
            self.assertEqual(self.compile(t).name, t.mapping_id)

    def test_composite_key_does_not_require_individually_unique_columns(self):
        table = copy.deepcopy(self.tables[0])
        table.keys = ["customer_id", "first_name"]
        self.assertFalse(self.compile(table).column("first_name")["unique"])

    def test_unknown_transform_rejected(self):
        for expression in (
            "evil(email)",
            "upper(email).__class__",
            "__import__('os')",
            "upper(email, **x)",
        ):
            with self.subTest(expression=expression), self.assertRaises((ValueError, SyntaxError)):
                parse_expression(expression)

    def test_transform_preserves_commas(self):
        self.assertEqual(
            evaluate(parse_expression("upper({name})"), {"name": "Smith, Jane"}, {}), "SMITH, JANE"
        )

    def test_null_stays_null(self):
        self.assertIsNone(evaluate(parse_expression("upper({name})"), {"name": None}, {}))

    def test_unmapped_code_fails(self):
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            evaluate(
                parse_expression("map(code, status)"), {"code": "bad"}, {"status": {"A": "active"}}
            )

    def test_explicit_date_format(self):
        self.assertEqual(
            evaluate(
                parse_expression("date_fmt(dob, '%Y-%m-%d', '%m/%d/%Y')"), {"dob": "12/31/1990"}, {}
            ),
            "1990-12-31",
        )

    def test_strict_string_comparison(self):
        spec = {"type": "string", "exact": True}
        self.assertNotEqual(normalize(" a ", spec), normalize("a", spec))
        self.assertNotEqual(normalize("", spec), normalize(None, spec))

    def test_empty_transform_frame(self):
        c = self.compile()
        self.assertEqual(
            list(mapped_rows(pd.DataFrame(columns=c.source_columns), c).columns),
            [col["name"] for col in c.columns],
        )

    def test_invalid_workbook_cells(self):
        cases = [
            ("Tables", "J2", "maybe"),
            ("Tables", "I2", 12.5),
            ("FieldMap", "D2", "unknown"),
            ("FieldMap", "H2", "=NOW()"),
            ("FieldMap", "E2", None),
            ("Tables", "A1", "broken"),
        ]
        for sheet, cell, value in cases:
            with self.subTest(sheet=sheet, cell=cell), tempfile.TemporaryDirectory() as tmp:
                wb = openpyxl.load_workbook(WORKBOOK)
                wb[sheet][cell] = value
                path = Path(tmp) / "bad.xlsx"
                wb.save(path)
                wb.close()
                with self.assertRaises(ValueError):
                    tables, codes = parser.load_workbook(str(path))
                    builder.build_contract(
                        tables[0], codes, {"type": "postgres"}, {"type": "snowflake"}
                    )

    def test_disabled_mapping_cannot_be_selected(self):
        self.assertFalse(
            next(t for t in self.tables if t.mapping_id == "legacy_card_migration").enabled
        )


class StagingTests(unittest.TestCase):
    def test_staging_cleans_up_after_failure(self):
        c = contract()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "null"):
                list(ordered_chunks(frames([(None, "a", "b")]), c, 2, tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_external_sort(self):
        from contextlib import closing

        c = contract()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                closing(
                    ordered_chunks(
                        frames([(20, "a", "b"), (1, "a", "b"), (2, "a", "b")]), c, 2, tmp
                    )
                ) as source,
                closing(
                    ordered_chunks(
                        frames([(2, "a", "b"), (20, "a", "b"), (1, "a", "b")]), c, 1, tmp
                    )
                ) as target,
            ):
                self.assertEqual(compare_chunked(source, target, c).mismatch_count, 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class SafetyAndEvidenceTests(unittest.TestCase):
    def test_query_rejects_mutations_before_execution(self):
        adapter = SnowflakeTarget.__new__(SnowflakeTarget)
        adapter.conn = MagicMock()
        for statement in (
            "DELETE FROM test",
            "SELECT 1; DROP TABLE test",
            "SELECT * INTO x FROM test",
        ):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                adapter.query(statement)
        adapter.conn.cursor.assert_not_called()

    def test_earlier_failures_remain_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = [
                {"mapping": "a", "stage": "recon", "status": "FAIL", "mismatches": 1},
                {"mapping": "b", "stage": "recon", "status": "PASS"},
            ]
            write_evidence(results, {"selected": ["a", "b"]}, tmp)
            data = json.loads((Path(tmp) / "run_summary.json").read_text())
            self.assertEqual(data["verdict"], "FAIL")
            self.assertEqual(data["coverage"], "PARTIAL")
            self.assertFalse(data["production_signoff"])

    def test_empty_run_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_evidence([], {}, tmp), "NOT_RUN")

    def test_excel_formula_injection_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_evidence([{"mapping": "=1+1", "stage": "recon", "status": "PASS"}], {}, tmp)
            wb = openpyxl.load_workbook(Path(tmp) / "migration_summary.xlsx")
            self.assertEqual(wb["Stage Results"]["A2"].data_type, "s")
            wb.close()


if __name__ == "__main__":
    unittest.main()
