import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pandas as pd
import yaml
from moto import mock_aws

from libs.adapters.base import TargetAdapter
from libs.adapters.s3_source import S3Source
from libs.engine import reconcile, rules, schema
from libs.migration import audit, controls, fingerprint
from libs.migration.manifest import REPO_ROOT, load_manifest
from libs.migration.masking import REDACTED, Masker

MANIFEST = REPO_ROOT / "config" / "migration" / "webster_to_santander.yaml"
ACCOUNT = REPO_ROOT / "config" / "contracts" / "migration" / "account.yaml"


def _write_yaml(folder: str, name: str, data: dict) -> Path:
    path = Path(folder) / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


class MaskingTests(unittest.TestCase):
    def test_redacts_without_key_and_passes_non_pii(self):
        m = Masker({"acct"})
        self.assertEqual(m.value("acct", "0012345678"), REDACTED)
        self.assertEqual(m.value("balance", Decimal("1.50")), "1.50")
        self.assertIsNone(m.value("acct", None))

    def test_keyed_tokens_are_stable_and_not_raw(self):
        m = Masker({"acct"}, key="k" * 32)
        token = m.value("acct", "0012345678")
        self.assertTrue(token.startswith("hmac:"))
        self.assertNotIn("0012345678", token)
        self.assertEqual(token, m.value("acct", " 0012345678 "))
        self.assertNotEqual(token, Masker({"acct"}, key="j" * 32).value("acct", "0012345678"))

    def test_short_key_rejected(self):
        with self.assertRaises(ValueError):
            Masker({"a"}, key="short")

    def test_composite_key_masks_only_pii_parts(self):
        m = Masker({"acct"})
        self.assertEqual(m.key(["acct", "seq"], ("001", 7)), (REDACTED, "7"))

    def test_contract_pii_covers_source_names(self):
        m = Masker.for_contract(schema.load_contract(str(ACCOUNT)), key=None)
        self.assertIn("account_number", m.pii_columns)
        self.assertIn("acct_no", m.pii_columns)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.contract = schema.load_contract(str(ACCOUNT))

    def test_map_transform_translates_and_unmapped_is_none(self):
        row = pd.Series({"status_cd": "A"})
        self.assertEqual(
            reconcile.apply_transform(
                "map(status_cd, account_status)", row, self.contract.raw["mappings"]
            ),
            "ACTIVE",
        )
        row = pd.Series({"status_cd": "X"})
        self.assertIsNone(
            reconcile.apply_transform(
                "map(status_cd, account_status)", row, self.contract.raw["mappings"]
            )
        )

    def test_unknown_mapping_name_fails(self):
        with self.assertRaises(ValueError):
            reconcile.map_code("A", "nope", {"x": {"A": "B"}})

    def test_mapping_rule_flags_unmapped_codes(self):
        df = pd.DataFrame(
            {
                "acct_no": ["0000000001", "0000000002"],
                "cust_id": ["1", "2"],
                "prod_cd": ["CHK01", "CHK01"],
                "ccy": ["USD", "USD"],
                "open_dt": ["2020-01-01", "2020-01-01"],
                "status_cd": ["A", "Z"],
                "cur_bal": ["1.00", "2.00"],
            }
        )
        failures = {f.rule_id: f for f in rules.evaluate_source_rules(df, self.contract)}
        self.assertEqual(failures["mapping:status"].failing_rows, 1)
        self.assertIn("mapping", rules.contract_rule_types(self.contract))

    def test_unquoted_yaml_codes_rejected(self):
        raw = yaml.safe_load(ACCOUNT.read_text(encoding="utf-8"))
        raw["mappings"]["account_status"] = {1: "ACTIVE"}
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, "quote"):
            schema.load_contract(str(_write_yaml(d, "c.yaml", raw)))

    def test_control_semantics_rejected(self):
        raw = yaml.safe_load(ACCOUNT.read_text(encoding="utf-8"))
        for bad in (
            {"id": "x", "type": "sum", "column": "currency"},
            {"id": "x", "type": "sum", "column": "nope"},
            {"id": "x", "type": "count", "column": "currency"},
            {"id": "x", "type": "count", "group_by": ["nope"]},
        ):
            raw["controls"] = [bad]
            with (
                self.subTest(bad=bad),
                tempfile.TemporaryDirectory() as d,
                self.assertRaises(ValueError),
            ):
                schema.load_contract(str(_write_yaml(d, "c.yaml", raw)))


class FakeTarget(TargetAdapter):
    dialect = "postgres"

    def __init__(self, rows=None):
        self.sql: list = []
        self.rows = rows or [(0,)]

    def _qualified(self, table):
        return f'"public"."{table}"'

    def _fetch(self, sql, max_rows):
        self.sql.append(sql)
        return self.rows

    read_table = row_count = schema = primary_key = lambda self, *a, **k: None
    is_closed = close = lambda self: None


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.contract = schema.load_contract(str(ACCOUNT))
        self.df = pd.DataFrame(
            {
                "account_number": ["1", "2", "3"],
                "currency": ["USD", "USD", "CAD"],
                "current_balance": ["0.10", "0.20", "5"],
                "status": ["ACTIVE", "ACTIVE", "CLOSED"],
            }
        )

    def test_ungrouped_count_counts_rows(self):
        ctl = {"id": "n", "type": "count"}
        self.assertEqual(controls.source_values(self.df, self.contract, ctl), {(): 3})

    def test_grouped_sum_is_exact_decimal(self):
        ctl = {"id": "s", "type": "sum", "column": "current_balance", "group_by": ["currency"]}
        values = controls.source_values(self.df, self.contract, ctl)
        self.assertEqual(values[("USD",)], Decimal("0.30"))
        self.assertEqual(values[("CAD",)], Decimal("5.00"))

    def test_target_mismatch_detected_per_group(self):
        ctl = {"id": "s", "type": "sum", "column": "current_balance", "group_by": ["currency"]}
        target = FakeTarget([("USD", Decimal("0.31")), ("CAD", Decimal("5.00"))])
        diffs = controls.compare_control(
            controls.source_values(self.df, self.contract, ctl),
            controls.target_values(target, self.contract, ctl),
            ctl,
        )
        self.assertEqual([d["group"] for d in diffs], [("USD",)])
        self.assertEqual(
            target.sql[0],
            'SELECT "currency", SUM("current_balance") FROM "public"."bank_account" GROUP BY "currency"',
        )

    def test_missing_target_group_is_a_difference(self):
        ctl = {"id": "c", "type": "count", "group_by": ["status"]}
        diffs = controls.compare_control(
            controls.source_values(self.df, self.contract, ctl),
            controls.target_values(FakeTarget([("ACTIVE", 2)]), self.contract, ctl),
            ctl,
        )
        self.assertEqual(
            diffs, [{"control": "c", "group": ("CLOSED",), "expected": 1, "actual": None}]
        )

    def test_pushdown_rejects_unknown_function_and_bad_identifiers(self):
        t = FakeTarget()
        with self.assertRaises(ValueError):
            t.aggregate("bank_account", "drop", "x")
        with self.assertRaises(ValueError):
            t.aggregate("bank_account", "sum", None)
        snowflake_like = type("SnowflakeLike", (FakeTarget,), {"dialect": "snowflake"})()
        with self.assertRaises(ValueError):
            snowflake_like.aggregate("t", "sum", "a; DROP TABLE b")
        with self.assertRaises(ValueError):
            snowflake_like.aggregate("t", "count", group_by=['x" OR 1=1 --'])

    def test_pushdown_result_bound_fails_closed(self):
        t = FakeTarget([(1,)] * (TargetAdapter.PUSHDOWN_MAX_ROWS + 1))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            t.aggregate("bank_account", "count")

    def test_orphan_sql_shape(self):
        t = FakeTarget([(2,)])
        t.orphans("bank_account", "customer_id", "bank_customer", "customer_id")
        self.assertIn('NOT EXISTS (SELECT 1 FROM "public"."bank_customer" pa', t.sql[0])

    def test_snowflake_pushdown_uses_generated_sql_only(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = ("RECON_RO_ROLE",)
        cur.fetchmany.return_value = [(3,)]
        with patch("snowflake.connector.connect", return_value=conn):
            from libs.adapters.snowflake_target import SnowflakeTarget

            sf = SnowflakeTarget(
                account="org-acct",
                warehouse="wh",
                database="recon",
                user="u",
                password="test-only",
                schema="public",
                role="recon_ro_role",
            )
            self.assertEqual(sf.aggregate("bank_account", "count"), [(3,)])
        self.assertEqual(
            cur.execute.call_args.args[0], 'SELECT COUNT(*) FROM "RECON"."PUBLIC"."BANK_ACCOUNT"'
        )
        with self.assertRaises(PermissionError):
            sf.query("SELECT 1")


@mock_aws
class S3HardeningTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="extract")
        self.s3.put_bucket_versioning(
            Bucket="extract", VersioningConfiguration={"Status": "Enabled"}
        )

    def put(self, key, body):
        self.s3.put_object(Bucket="extract", Key=key, Body=body.encode())

    def test_lineage_and_suffix_filter(self):
        self.put("t/part-1.csv", "a,b\n1,2\n")
        self.put("t/part-2.csv", "a,b\n3,4\n")
        self.put("t/_SUCCESS", "")
        src = S3Source(bucket="extract", prefix="t/", suffix=".csv")
        self.assertEqual(src.row_count(), 2)
        lineage = src.lineage()
        self.assertEqual([o["key"] for o in lineage], ["t/part-1.csv", "t/part-2.csv"])
        self.assertTrue(all(o["etag"] and o["version_id"] for o in lineage))

    def test_empty_prefix_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "matched no objects"):
            S3Source(bucket="extract", prefix="missing/")

    def test_part_header_mismatch_fails(self):
        self.put("t/part-1.csv", "a,b\n1,2\n")
        self.put("t/part-2.csv", "b,a\n3,4\n")
        with self.assertRaisesRegex(ValueError, "header differs"):
            S3Source(bucket="extract", prefix="t/")

    def test_max_objects_bound(self):
        for i in range(3):
            self.put(f"t/p{i}.csv", "a\n1\n")
        with self.assertRaisesRegex(ValueError, "max_objects"):
            S3Source(bucket="extract", prefix="t/", max_objects=2)

    def test_config_validation(self):
        for kwargs in (
            {"key": "a", "prefix": "b"},
            {"prefix": "t/", "version_id": "v"},
            {"key": "a", "expected_bucket_owner": "123"},
            {"key": "a", "endpoint_url": "http://s3.example.com"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                S3Source(bucket="extract", **kwargs)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.raw = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, raw):
        return load_manifest(_write_yaml(self.tmp.name, "m.yaml", raw))

    def test_real_manifest_orders_parents_first(self):
        m = load_manifest(MANIFEST)
        self.assertEqual(m.enabled_tables(), ["customer", "account", "transaction"])
        self.assertEqual(len(m.file_hashes()), 4)

    def test_rejections(self):
        cases = {
            "duplicate manifest table": lambda r: r["tables"].append(dict(r["tables"][0])),
            "unknown tables": lambda r: r["tables"][0].update(depends_on=["ghost"]),
            "cycle": lambda r: r["tables"][0].update(depends_on=["transaction"]),
            "escapes the repository": lambda r: r["tables"][0].update(contract="../outside.yaml"),
            "contract_name": lambda r: r["tables"][0].update(name="client"),
            "has no column": lambda r: r["relationships"][0].update(column="nope"),
            "types differ": lambda r: r["relationships"][0].update(column="currency"),
            "is disabled": lambda r: r["tables"][0].update(enabled=False),
        }
        for message, mutate in cases.items():
            raw = yaml.safe_load(yaml.safe_dump(self.raw))
            mutate(raw)
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.load(raw)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)

    def run_for(self):
        return audit.MigrationRun(self.manifest, "local-test", "postgres", "redacted", REPO_ROOT)

    def test_missing_checks_are_incomplete(self):
        run = self.run_for()
        for name in self.manifest.enabled_tables():
            for check in audit.REQUIRED_TABLE_CHECKS[:-1]:
                run.record(name, check, "PASS")
        for rel in self.manifest.relationships:
            run.record_relationship(rel["id"], "PASS")
        self.assertEqual(run.overall_status(), "INCOMPLETE")
        for name in self.manifest.enabled_tables():
            run.record(name, "records", "PASS")
        self.assertEqual(run.overall_status(), "PASS")
        run.record("account", "records", "SKIPPED")
        self.assertEqual(run.overall_status(), "FAIL")

    def test_report_hash_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.run_for().write(d)
            self.assertTrue(audit.verify_report(path))
            path.write_text(
                path.read_text(encoding="utf-8").replace("INCOMPLETE", "PASS"), encoding="utf-8"
            )
            self.assertFalse(audit.verify_report(path))


class ExpanderTests(unittest.TestCase):
    def test_expands_per_table_and_relationship(self):
        from robot.api import TestSuite

        from libs.robot.migration_expander import migration_expander

        suite = TestSuite(name="m")
        t = suite.tests.create(name="Rows", tags=["per-table"])
        t.body.create_keyword(name="Verify Row Count", args=["${TABLE}"])
        r = suite.tests.create(name="RI", tags=["per-relationship"])
        r.body.create_keyword(name="Verify Relationship", args=["${RELATIONSHIP}"])
        suite.tests.create(name="Plain")
        suite.visit(migration_expander(str(MANIFEST)))
        names = [x.name for x in suite.tests]
        self.assertEqual(names[:3], ["customer :: Rows", "account :: Rows", "transaction :: Rows"])
        self.assertIn("transaction_account :: RI", names)
        self.assertIn("Plain", names)
        self.assertEqual(suite.tests[1].body[0].args, ("account",))
        self.assertIn("tier:critical", suite.tests[1].tags)
        self.assertTrue(suite.metadata["Migration Manifest"].endswith("webster_to_santander.yaml"))


class LocalGuardTests(unittest.TestCase):
    def test_fixture_keywords_refuse_non_local_environment(self):
        from libs.robot.MigrationLibrary import MigrationLibrary

        lib = MigrationLibrary()
        lib.manifest = load_manifest(MANIFEST)
        lib.env = {
            "environment": "uat",
            "local_fixture": {"s3_port": 1},
            "source_connection": {"endpoint_url": "http://127.0.0.1:1"},
            "target": {"type": "postgres", "host": "localhost"},
        }
        with self.assertRaises(PermissionError):
            lib.execute_local_target_sql("DELETE FROM x")
        lib.env["environment"] = "local-x"
        lib.env["target"]["host"] = "db.bank.internal"
        with self.assertRaises(PermissionError):
            lib.seed_local_target()

    def test_classification_gate(self):
        from libs.robot.MigrationLibrary import MigrationLibrary

        raw = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        raw["data_classification"] = "confidential"
        with tempfile.TemporaryDirectory() as d:
            manifest = _write_yaml(d, "m.yaml", raw)
            env = REPO_ROOT / "config" / "environments" / "migration_local.yaml"
            with self.assertRaisesRegex(PermissionError, "does not allow"):
                MigrationLibrary().load_migration(str(manifest), str(env))


class FingerprintTests(unittest.TestCase):
    CONTRACT = ContractT = None  # built in setUpClass

    @classmethod
    def setUpClass(cls):
        from libs.engine.models import Contract

        cls.CONTRACT = Contract(
            raw={
                "contract_name": "fp_test",
                "version": "1",
                "source": {"columns": []},
                "target": {"table": "fp_t"},
                "keys": ["k"],
                "columns": [
                    {"name": "k", "type": "string"},
                    {"name": "amt", "type": "decimal", "scale": 2},
                    {"name": "dt", "type": "date"},
                    {"name": "ts", "type": "timestamp"},
                    {"name": "flag", "type": "boolean"},
                    {"name": "note", "type": "string"},
                ],
            },
            path="fp_test",
        )

    def _df(self, rows):
        return pd.DataFrame(
            rows,
            columns=["k", "amt", "dt", "ts", "flag", "note"],
        )

    def _row(self, k="001", amt="10.00", note="x"):
        import datetime as dt

        return [k, amt, dt.date(2024, 1, 5), "2024-01-05 06:07:08", "true", note]

    def test_canonical_value_forms(self):
        dec = {"name": "amt", "type": "decimal", "scale": 2}
        self.assertEqual(fingerprint.canonical_value("100.1", dec), "100.10")
        self.assertEqual(fingerprint.canonical_value("-5", dec), "-5.00")
        self.assertEqual(
            fingerprint.canonical_value(None, {"name": "n", "type": "string"}),
            fingerprint.NULL_SENTINEL,
        )
        self.assertEqual(
            fingerprint.canonical_value("2024-01-05", {"name": "d", "type": "date"}),
            "2024-01-05",
        )
        self.assertEqual(
            fingerprint.canonical_value("2024-01-05 06:07:08", {"name": "t", "type": "timestamp"}),
            "2024-01-05 06:07:08.000000",
        )
        self.assertEqual(
            fingerprint.canonical_value("yes", {"name": "b", "type": "boolean"}), "true"
        )

    def test_bucket_checksums_detect_single_value_change(self):
        df = self._df([self._row(f"{i:05d}") for i in range(50)])
        base = fingerprint.source_checksums(df, self.CONTRACT, 32)
        tampered = df.copy()
        tampered.loc[0, "amt"] = "10.01"
        diff = fingerprint.compare_checksums(
            base, fingerprint.source_checksums(tampered, self.CONTRACT, 32)
        )
        self.assertEqual(len(diff), 1)
        self.assertNotEqual(diff[0]["expected"][1], diff[0]["actual"][1])
        self.assertEqual(diff[0]["expected"][0], diff[0]["actual"][0])  # count same

    def test_bucket_checksums_detect_deleted_row(self):
        df = self._df([self._row(f"{i:05d}") for i in range(50)])
        base = fingerprint.source_checksums(df, self.CONTRACT, 32)
        diff = fingerprint.compare_checksums(
            base, fingerprint.source_checksums(df.iloc[1:], self.CONTRACT, 32)
        )
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0]["actual"][0], diff[0]["expected"][0] - 1)

    def test_clean_data_produces_no_bucket_diffs(self):
        df = self._df([self._row(f"{i:05d}") for i in range(50)])
        sums = fingerprint.source_checksums(df, self.CONTRACT, 256)
        self.assertEqual(fingerprint.compare_checksums(sums, dict(sums)), [])
        self.assertEqual(sum(n for n, _ in sums.values()), 50)

    def test_null_and_duplicate_keys_rejected(self):
        df = self._df([self._row("001"), self._row("001")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fingerprint.source_checksums(df, self.CONTRACT, 32)
        df2 = self._df([self._row("001"), self._row(None)])
        with self.assertRaisesRegex(ValueError, "null"):
            fingerprint.source_checksums(df2, self.CONTRACT, 32)

    def test_source_rows_in_bucket_returns_only_that_bucket(self):
        df = self._df([self._row(f"{i:05d}") for i in range(50)])
        sums = fingerprint.source_checksums(df, self.CONTRACT, 8)
        for bucket, (count, _) in sums.items():
            rows = fingerprint.source_rows_in_bucket(df, self.CONTRACT, 8, bucket)
            self.assertEqual(len(rows), count)
        self.assertEqual(
            sum(
                len(r)
                for r in [fingerprint.source_rows_in_bucket(df, self.CONTRACT, 8, b) for b in sums]
            ),
            50,
        )

    def test_generated_sql_is_dialect_shaped_and_bounded(self):
        for dialect in ("postgres", "mysql", "snowflake"):
            sql = fingerprint.bucket_checksum_sql(self.CONTRACT, dialect, "t", 64)
            self.assertIn("GROUP BY", sql)
            self.assertIn("MD5(", sql.upper())
            self.assertIn("COUNT(*)", sql)
            self.assertNotIn(";", sql)
        with self.assertRaises(ValueError):
            fingerprint.canonical_sql({"name": "x", "type": "xml"}, "postgres")
        with self.assertRaises(ValueError):
            fingerprint.bucket_checksum_sql(self.CONTRACT, "sqlite", "t", 8)


class ControlFileTests(unittest.TestCase):
    """MIG-P4: bank trailer files — source-domain groups translated via map()."""

    @classmethod
    def setUpClass(cls):
        cls.contract = schema.load_contract(str(ACCOUNT))

    def _ctl_df(self, rows):
        return pd.DataFrame(rows, columns=["name", "group", "value"])

    def test_file_values_translate_source_codes(self):
        contract = self.contract
        ctl = next(c for c in contract.raw["controls"] if c["id"] == "accounts_by_status")
        df = self._ctl_df([("accounts_by_status", "A", "339"), ("accounts_by_status", "C", "33")])
        self.assertEqual(
            controls.file_values(df, contract, ctl), {("ACTIVE",): 339, ("CLOSED",): 33}
        )

    def test_file_values_ungrouped_and_decimal(self):
        contract = self.contract
        ctl = next(c for c in contract.raw["controls"] if c["id"] == "row_count")
        df = self._ctl_df([("row_count", "", "400")])
        self.assertEqual(controls.file_values(df, contract, ctl), {(): 400})
        bal = next(c for c in contract.raw["controls"] if c["id"] == "max_balance")
        df = self._ctl_df([("max_balance", "", "249998.76")])
        self.assertEqual(controls.file_values(df, contract, bal), {(): Decimal("249998.76")})

    def test_unmapped_file_group_rejected(self):
        ctl = next(c for c in self.contract.raw["controls"] if c["id"] == "accounts_by_status")
        df = self._ctl_df([("accounts_by_status", "X", "1")])
        with self.assertRaisesRegex(ValueError, "not in mapping"):
            controls.file_values(df, self.contract, ctl)

    def test_duplicate_and_wrong_group_arity_rejected(self):
        ctl = next(c for c in self.contract.raw["controls"] if c["id"] == "accounts_by_status")
        dup = self._ctl_df([("accounts_by_status", "A", "1"), ("accounts_by_status", "A", "2")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            controls.file_values(dup, self.contract, ctl)
        grp = next(
            c for c in self.contract.raw["controls"] if c["id"] == "balance_by_product_currency"
        )
        bad = self._ctl_df([("balance_by_product_currency", "CHK01", "1.00")])
        with self.assertRaisesRegex(ValueError, "group values"):
            controls.file_values(bad, self.contract, grp)

    def test_compare_file_detects_mismatch_and_extra(self):
        ctl = {"id": "row_count"}
        self.assertEqual(
            controls.compare_file({(): 400}, {(): 401}, ctl)[0]["control"], "row_count"
        )
        self.assertEqual(controls.compare_file({(): 400}, {(): 400}, ctl), [])
        df = self._ctl_df([("row_count", "", "1"), ("undeclared", "", "9")])
        self.assertEqual(controls.declared_names(df), {"row_count", "undeclared"})


if __name__ == "__main__":
    unittest.main()
