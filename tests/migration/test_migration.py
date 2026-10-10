import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pandas as pd
from test_streaming import contract, frames

from libs.adapters.postgres_source import PostgresSource
from libs.adapters.snowflake_source import SnowflakeSource
from libs.adapters.snowflake_target import SnowflakeTarget
from libs.adapters.staging import ordered_chunks
from libs.engine.mapping_transforms import evaluate, mapped_rows, parse_expression
from libs.engine.reconcile import compare_chunked, normalize
from libs.mapping import builder, parser
from libs.reporting.report import write_evidence
from libs.robot.MigrationLibrary import MigrationLibrary

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


def _library(env):
    lib = MigrationLibrary.__new__(MigrationLibrary)
    lib.env = copy.deepcopy(env)
    lib.meta = {}
    lib._snapshot_holder = None
    return lib


class SnapshotTests(unittest.TestCase):
    ENV = {
        "source": {
            "type": "postgres",
            "host": "h",
            "port": 5432,
            "database": "d",
            "user": "u",
            "password": "p",
        },
        "target": {"type": "snowflake", "fake": True},
        "run": {"snapshot_id": "cut-2026-01-01"},
    }

    def test_malformed_exported_snapshot_rejected_before_connect(self):
        for bad in ("0001; DROP TABLE t", "snap 1", "'x'", ""):
            with (
                self.subTest(snapshot=bad),
                self.assertRaises(ValueError),
                patch("psycopg2.connect") as connect,
            ):
                PostgresSource(
                    host="h",
                    port=5432,
                    database="d",
                    user="u",
                    password="p",
                    table="t",
                    snapshot=bad,
                )
            connect.assert_not_called()

    def test_watermark_requires_value_before_connect(self):
        with (
            self.assertRaisesRegex(ValueError, "snapshot_value"),
            patch("psycopg2.connect") as connect,
        ):
            PostgresSource(
                host="h",
                port=5432,
                database="d",
                user="u",
                password="p",
                table="t",
                snapshot_column="updated_at",
            )
        connect.assert_not_called()

    def test_postgres_watermark_predicate_is_bound(self):
        src = PostgresSource.__new__(PostgresSource)
        src.schema_name, src.table, src.columns = "s", "t", ["a"]
        src.where, src.snapshot_column, src._params = None, "updated_at", ("cut1",)
        self.assertEqual(src._select_sql(), 'SELECT "a" FROM "s"."t" WHERE "updated_at" <= %s')
        self.assertEqual(src._params, ("cut1",))

    def test_snowflake_watermark_combines_with_scope(self):
        src = SnowflakeSource.__new__(SnowflakeSource)
        src.target = MagicMock()
        src.target._qualified.return_value = '"SANTANDER"."PUBLIC"."T"'
        src.table, src.columns = "T", ["a"]
        src.where, src.snapshot_column, src._params = "x = 1", '"TS"', ("cut1",)
        sql = src._select()
        self.assertIn('(x = 1) AND "TS" <= %s', sql)
        self.assertEqual(src._params, ("cut1",))

    def test_exported_snapshot_pins_holder_and_source(self):
        lib = _library({**self.ENV, "source": {**self.ENV["source"], "snapshot": "exported"}})
        holder = MagicMock()
        holder.cursor.return_value.fetchone.return_value = ("0000000A-00000C7B-1",)
        with patch("psycopg2.connect", return_value=holder) as connect:
            lib._bind_snapshot()
        holder.set_session.assert_called_once_with(
            readonly=True, autocommit=False, isolation_level="REPEATABLE READ"
        )
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(lib.env["source"]["snapshot"], "0000000A-00000C7B-1")
        self.assertTrue(lib.meta["snapshot_enforced"])
        self.assertIs(lib._snapshot_holder, holder)

    def test_exported_snapshot_is_postgres_only(self):
        lib = _library({**self.ENV, "source": {"type": "snowflake", "snapshot": "exported"}})
        with self.assertRaisesRegex(ValueError, "postgres-only"):
            lib._bind_snapshot()

    def test_watermark_binding_uses_snapshot_id(self):
        lib = _library(
            {
                **self.ENV,
                "source": {**self.ENV["source"], "snapshot_column": "updated_at"},
            }
        )
        lib._bind_snapshot()
        self.assertEqual(lib.env["source"]["snapshot_value"], "cut-2026-01-01")
        self.assertEqual(lib.meta["source_snapshot"], "watermark:updated_at")
        self.assertTrue(lib.meta["snapshot_enforced"])

    def test_watermark_without_snapshot_id_fails(self):
        lib = _library(
            {
                **self.ENV,
                "run": {},
                "source": {**self.ENV["source"], "snapshot_column": "updated_at"},
            }
        )
        with self.assertRaisesRegex(ValueError, "snapshot_id"):
            lib._bind_snapshot()

    def test_real_target_refuses_unenforced_snapshot(self):
        lib = _library({**self.ENV, "target": {"type": "snowflake", "fake": False}})
        with self.assertRaisesRegex(ValueError, "enforceable snapshot"):
            lib._bind_snapshot()

    def test_emulated_run_records_unenforced_snapshot(self):
        lib = _library({**self.ENV, "source": dict(self.ENV["source"])})
        lib._bind_snapshot()
        self.assertFalse(lib.meta["snapshot_enforced"])
        self.assertEqual(lib.meta["snapshot_id"], "cut-2026-01-01")


if __name__ == "__main__":
    unittest.main()
