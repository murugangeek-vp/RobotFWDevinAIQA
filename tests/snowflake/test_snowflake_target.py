import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd

from libs.adapters.snowflake_target import SnowflakeTarget
from libs.engine import reconcile, schema
from libs.loader import load_expected_rows
from libs.robot.ReconciliationLibrary import ReconciliationLibrary, _expand_env


class SnowflakeTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.conn.is_closed.return_value = False
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = ("RECON_RO_ROLE",)
        self.conn.cursor.return_value = self.cursor
        self.connect = patch("snowflake.connector.connect", return_value=self.conn).start()
        self.addCleanup(patch.stopall)
        self.cfg = dict(
            account="org-account",
            warehouse="recon_wh",
            database="recon",
            schema="public",
            role="recon_ro_role",
            user="test_user",
            password="test-only",
        )
        self.contract = schema.load_contract("config/contracts/customer_snowflake.yaml")

    def adapter(self, **kwargs):
        return SnowflakeTarget(**(self.cfg | kwargs))

    def test_requires_explicit_role(self):
        with self.assertRaises(ValueError):
            self.adapter(role=None)
        self.connect.assert_not_called()

    def test_identifiers_reject_sql_fragments(self):
        for value in ("a; DROP TABLE b", '"Mixed"', "a.b", "a b", "", "a--x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                schema.ident(value, "snowflake")

    def test_primary_key_uses_show_and_sequence(self):
        adapter = self.adapter()
        self.cursor.description = [("column_name",), ("key_sequence",)]
        self.cursor.fetchall.return_value = [("SECOND", 2), ("FIRST", 1)]
        self.assertEqual(adapter.primary_key("customer"), ["first", "second"])
        self.assertEqual(
            self.cursor.execute.call_args.args[0],
            'SHOW PRIMARY KEYS IN TABLE "RECON"."PUBLIC"."CUSTOMER"',
        )

    def test_query_rejected_without_execution(self):
        adapter = self.adapter()
        self.cursor.reset_mock()
        with self.assertRaises(PermissionError):
            adapter.query("DELETE FROM customer")
        self.cursor.execute.assert_not_called()

    def test_loader_disabled_before_cursor_creation(self):
        with self.assertRaises(PermissionError):
            load_expected_rows(self.conn, self.contract, pd.DataFrame(), "snowflake")
        self.conn.cursor.assert_not_called()

    def test_missing_environment_reference_fails(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            _expand_env({"account": "${SNOWFLAKE_ACCOUNT}"})

    def test_duplicate_target_keys_are_not_collapsed(self):
        contract = MagicMock(keys=["id"], columns=[{"name": "id", "type": "integer"}])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reconcile.compare(pd.DataFrame({"id": [1]}), pd.DataFrame({"id": [1, 1]}), contract)

    def test_null_keys_are_rejected(self):
        contract = MagicMock(keys=["id"], columns=[{"name": "id", "type": "integer"}])
        with self.assertRaisesRegex(ValueError, "null"):
            reconcile.compare(pd.DataFrame({"id": [None]}), pd.DataFrame({"id": [None]}), contract)

    def test_fractional_integer_is_not_truncated(self):
        with self.assertRaises(ValueError):
            reconcile.normalize(Decimal("1.5"), {"type": "integer"})

    def test_robot_sql_cannot_bypass_adapter(self):
        library = ReconciliationLibrary()
        library.target = self.adapter()
        self.cursor.reset_mock()
        with self.assertRaises(PermissionError):
            library.execute_read_only_sql("DELETE FROM customer")
        self.cursor.execute.assert_not_called()

    def test_connection_security_and_timeouts(self):
        self.adapter()
        args = self.connect.call_args.kwargs
        self.assertEqual(args["role"], "RECON_RO_ROLE")
        self.assertEqual(args["database"], "RECON")
        self.assertEqual(args["login_timeout"], 30)
        self.assertEqual(args["network_timeout"], 60)
        self.assertFalse(args["ocsp_fail_open"])
        self.assertEqual(args["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"], 120)
        self.assertEqual(self.cursor.execute.call_args_list[0].args, ("USE SECONDARY ROLES NONE",))

    def test_role_mismatch_closes_connection(self):
        self.cursor.fetchone.return_value = ("ACCOUNTADMIN",)
        with self.assertRaises(PermissionError):
            self.adapter()
        self.conn.close.assert_called_once()
        self.cursor.close.assert_called_once()

    def test_initialization_error_closes_connection(self):
        self.cursor.execute.side_effect = RuntimeError("session setup failed")
        with self.assertRaisesRegex(RuntimeError, "session setup failed"):
            self.adapter()
        self.conn.close.assert_called_once()
        self.cursor.close.assert_called_once()

    def test_missing_password_fails_before_connect(self):
        with self.assertRaises(ValueError):
            self.adapter(password="")
        self.connect.assert_not_called()

    def test_key_pair_auth_does_not_forward_password(self):
        with patch.dict(
            os.environ,
            {
                "SNOWFLAKE_PRIVATE_KEY_FILE": "test-key.p8",
                "SNOWFLAKE_PRIVATE_KEY_PASSWORD": "test-passphrase",
            },
        ):
            self.adapter(authenticator="SNOWFLAKE_JWT")
        args = self.connect.call_args.kwargs
        self.assertNotIn("password", args)
        self.assertEqual(args["private_key_file"], "test-key.p8")
        self.assertEqual(args["private_key_file_pwd"], "test-passphrase")

    def test_unencrypted_key_does_not_forward_passphrase(self):
        with patch.dict(os.environ, {"SNOWFLAKE_PRIVATE_KEY_FILE": "test-key.p8"}, clear=True):
            self.adapter(authenticator="SNOWFLAKE_JWT")
        self.assertNotIn("private_key_file_pwd", self.connect.call_args.kwargs)

    def test_empty_current_role_fails_closed(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(PermissionError):
            self.adapter()
        self.conn.close.assert_called_once()

    def test_missing_key_pair_fails_before_connect(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            self.adapter(authenticator="SNOWFLAKE_JWT")
        self.connect.assert_not_called()

    def test_unknown_authenticator_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter(authenticator="unknown")
        self.connect.assert_not_called()

    def test_invalid_limits_rejected(self):
        for name in ("login_timeout", "network_timeout", "statement_timeout", "max_rows"):
            for value in (0, -1, True, "10", 0.5):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.adapter(**{name: value})
        self.connect.assert_not_called()

    def test_invalid_connection_coordinates_rejected(self):
        for name in ("account", "user", "database", "schema", "warehouse", "role"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.adapter(**{name: ""})
        self.connect.assert_not_called()

    def test_read_preserves_decimal_null_and_integer(self):
        adapter = self.adapter()
        self.cursor.description = [("ID",), ("BALANCE",)]
        value = Decimal("1234567890123456.12")
        self.cursor.fetchmany.return_value = [(1, value), (2, None)]
        frame = adapter.read_table("customer", ["id", "balance"])
        self.assertEqual(list(frame.columns), ["id", "balance"])
        self.assertEqual(frame.iloc[0]["balance"], value)
        self.assertIsNone(frame.iloc[1]["balance"])
        self.assertIn(
            'SELECT "ID", "BALANCE" FROM "RECON"."PUBLIC"."CUSTOMER"',
            self.cursor.execute.call_args.args[0],
        )

    def test_read_empty_result_preserves_columns(self):
        adapter = self.adapter()
        self.cursor.description = [("ID",)]
        self.cursor.fetchmany.return_value = []
        frame = adapter.read_table("customer")
        self.assertEqual(list(frame.columns), ["id"])
        self.assertTrue(frame.empty)

    def test_read_limit_fails_instead_of_partial_success(self):
        adapter = self.adapter(max_rows=1)
        self.cursor.description = [("ID",)]
        self.cursor.fetchmany.return_value = [(1,), (2,)]
        self.cursor.close.reset_mock()
        with self.assertRaisesRegex(ValueError, "exceeds max_rows"):
            adapter.read_table("customer")
        self.assertIn("LIMIT 2", self.cursor.execute.call_args.args[0])
        self.cursor.close.assert_called_once()

    def test_read_failure_closes_cursor(self):
        adapter = self.adapter()
        self.cursor.execute.side_effect = RuntimeError("query failed")
        self.cursor.close.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            adapter.read_table("customer")
        self.cursor.close.assert_called_once()

    def test_mixed_case_column_rejected(self):
        adapter = self.adapter()
        self.cursor.description = [("Mixed",)]
        with self.assertRaisesRegex(ValueError, "case-sensitive"):
            adapter.read_table("customer")

    def test_duplicate_result_column_rejected(self):
        adapter = self.adapter()
        self.cursor.description = [("ID",), ("ID",)]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            adapter.read_table("customer")

    def test_invalid_table_and_column_never_execute(self):
        adapter = self.adapter()
        self.cursor.reset_mock()
        for action in (
            lambda: adapter.read_table("x;drop"),
            lambda: adapter.read_table("customer", ["x;drop"]),
            lambda: adapter.row_count("x;drop"),
            lambda: adapter.schema("x;drop"),
            lambda: adapter.primary_key("x;drop"),
        ):
            with self.assertRaises(ValueError):
                action()
        self.cursor.execute.assert_not_called()

    def test_schema_uses_bound_filters(self):
        adapter = self.adapter()
        self.cursor.fetchall.return_value = [("ID", "NUMBER", "NO", 38, 0, None)]
        self.assertEqual(adapter.schema("customer"), [("id", "number", False)])
        args = self.cursor.execute.call_args.args
        self.assertIn('"RECON".INFORMATION_SCHEMA.COLUMNS', args[0])
        self.assertEqual(args[1], ("PUBLIC", "CUSTOMER"))

    def test_type_details_detect_fractional_integer_scale_and_length(self):
        adapter = self.adapter()
        self.cursor.fetchall.return_value = [
            ("CUSTOMER_ID", "NUMBER", "NO", 38, 2, None),
            ("BALANCE", "NUMBER", "NO", 18, 0, None),
            ("FULL_NAME", "TEXT", "NO", None, None, 100),
        ]
        adapter.schema("customer")
        errors = adapter.type_errors(self.contract)
        self.assertEqual(len(errors), 3)
        self.assertIn("numeric_scale=0", errors[0])
        self.assertIn("scale mismatch", errors[1] if "scale mismatch" in errors[1] else errors[2])

    def test_declared_precision_is_checked(self):
        adapter = self.adapter()
        self.cursor.fetchall.return_value = [("BALANCE", "NUMBER", "NO", 10, 2, None)]
        adapter.schema("customer")
        contract = MagicMock(
            columns=[{"name": "balance", "type": "decimal", "scale": 2, "precision": 18}]
        )
        self.assertIn("precision mismatch", adapter.type_errors(contract)[0])

    def test_missing_primary_key_returns_empty(self):
        adapter = self.adapter()
        self.cursor.description = [("column_name",), ("key_sequence",)]
        self.cursor.fetchall.return_value = []
        self.assertEqual(adapter.primary_key("customer"), [])

    def test_count_closes_cursor(self):
        adapter = self.adapter()
        self.cursor.fetchone.return_value = (42,)
        self.cursor.close.reset_mock()
        self.assertEqual(adapter.row_count("customer"), 42)
        self.cursor.close.assert_called_once()

    def test_close_is_idempotent(self):
        adapter = self.adapter()
        self.conn.is_closed.side_effect = [False, True]
        adapter.close()
        adapter.close()
        self.conn.close.assert_called_once()

    def test_registry_target_and_disabled_writer(self):
        from libs.adapters.registry import create_target, create_writer

        adapter = create_target({"type": "snowflake", **self.cfg}, "test_user", "test-only")
        self.assertIsInstance(adapter, SnowflakeTarget)
        with self.assertRaises(PermissionError):
            create_writer({"type": "snowflake"}, "test_user", "test-only")

    def test_library_write_paths_blocked_before_credentials(self):
        library = ReconciliationLibrary()
        library.env = {"target": {"type": "snowflake"}}
        with patch.object(library, "_creds") as creds:
            with self.assertRaises(PermissionError):
                library.load_source_into_target()
            with self.assertRaises(PermissionError):
                library.execute_write_sql("TRUNCATE TABLE customer")
        creds.assert_not_called()

    def test_library_key_pair_auth_does_not_require_password(self):
        library = ReconciliationLibrary()
        library.env = {"target": {"type": "snowflake", "authenticator": "SNOWFLAKE_JWT"}}
        with patch.dict(os.environ, {"RECON_RO_USER": "test_user"}, clear=True):
            self.assertEqual(library._creds("RO"), ("test_user", ""))

    def test_library_schema_mismatch_fails_before_connect(self):
        library = ReconciliationLibrary()
        library.env = {"target": {"type": "snowflake", "schema": "OTHER"}}
        library.contract = self.contract
        with self.assertRaisesRegex(ValueError, "schemas differ"):
            library.connect_target_read_only()
        self.connect.assert_not_called()

    def test_expansion_preserves_literals_and_nested_values(self):
        with patch.dict(os.environ, {"SF_TEST": "resolved"}):
            self.assertEqual(
                _expand_env({"list": ["${SF_TEST}", 1, "literal"]}),
                {"list": ["resolved", 1, "literal"]},
            )

    def test_normalized_duplicate_source_keys_rejected(self):
        contract = MagicMock(keys=["id"], columns=[{"name": "id", "type": "integer"}])
        with self.assertRaisesRegex(ValueError, "source contains duplicate"):
            reconcile.compare(
                pd.DataFrame({"id": ["01", "1"]}), pd.DataFrame({"id": [1]}), contract
            )

    def test_composite_keys_reconcile_without_order_dependency(self):
        contract = MagicMock(
            keys=["id", "part"],
            columns=[
                {"name": "id", "type": "integer"},
                {"name": "part", "type": "string"},
                {"name": "value", "type": "decimal", "scale": 2},
            ],
        )
        source = pd.DataFrame({"id": ["1", "1"], "part": ["a", "b"], "value": ["1.23", "2.34"]})
        actual = pd.DataFrame(
            {"id": [1, 1], "part": ["b", "a"], "value": [Decimal("2.34"), Decimal("1.23")]}
        )
        self.assertEqual(reconcile.compare(source, actual, contract).mismatch_count, 0)

    def test_unknown_dialect_never_defaults_to_postgres(self):
        with self.assertRaises(ValueError):
            schema.ddl_for_contract(self.contract, "unknown")
        adapter = MagicMock(dialect="unknown")
        with self.assertRaises(ValueError):
            schema.validate_target_schema_by_category(adapter, self.contract)
        adapter.schema.assert_not_called()

    def test_timezone_bearing_schema_is_not_silently_accepted(self):
        self.assertNotIn("timestamp_ltz", schema.TYPE_ALIASES["snowflake"]["timestamp"])
        self.assertNotIn("timestamp_tz", schema.TYPE_ALIASES["snowflake"]["timestamp"])

    def test_schema_missing_table_reports_every_aspect(self):
        adapter = MagicMock(dialect="snowflake")
        adapter.schema.return_value = []
        errors = schema.validate_target_schema_by_category(adapter, self.contract)
        self.assertEqual(set(errors), set(schema.SCHEMA_ASPECTS))
        self.assertTrue(all(errors.values()))

    def test_csv_to_mocked_snowflake_record_reconciliation(self):
        source = pd.read_csv("data/samples/customer.csv", dtype=str, keep_default_na=False)
        expected = reconcile.expected_target_rows(source, self.contract)
        adapter = self.adapter()
        columns = list(expected.columns)
        rows = [
            tuple(reconcile.normalize(row[col["name"]], col) for col in self.contract.columns)
            for _, row in expected.iterrows()
        ]
        self.cursor.description = [(name.upper(),) for name in columns]
        self.cursor.fetchmany.return_value = list(reversed(rows))
        actual = adapter.read_table("customer", columns)
        self.assertEqual(reconcile.compare(expected, actual, self.contract).mismatch_count, 0)
        actual.loc[0, "full_name"] = "deliberately incorrect"
        result = reconcile.compare(expected, actual, self.contract)
        self.assertEqual(len(result.diffs), 1)
        self.assertEqual(result.diffs[0].column, "full_name")
        missing = reconcile.compare(expected, actual.iloc[1:], self.contract)
        self.assertEqual(len(missing.missing_in_target), 1)
        extra = actual.iloc[:1].copy()
        extra["customer_id"] = 999999
        result = reconcile.compare(expected, pd.concat([actual, extra]), self.contract)
        self.assertEqual(result.extra_in_target, [999999])

    def test_postgres_mysql_identifier_escaping_preserved(self):
        self.assertEqual(schema.ident('a"b', "postgres"), '"a""b"')
        self.assertEqual(schema.ident("a`b", "mysql"), "`a``b`")

    def test_all_schema_aspects_with_connector_metadata(self):
        adapter = self.adapter()
        metadata = []
        for col in self.contract.columns:
            dtype = {
                "integer": "NUMBER",
                "decimal": "NUMBER",
                "string": "TEXT",
                "date": "DATE",
                "timestamp": "TIMESTAMP_NTZ",
            }[col["type"]]
            scale = 0 if col["type"] == "integer" else col.get("scale")
            metadata.append(
                (
                    col["name"].upper(),
                    dtype,
                    "YES" if col["nullable"] else "NO",
                    38,
                    scale,
                    col.get("max_length"),
                )
            )
        self.cursor.fetchall.side_effect = [metadata, [("CUSTOMER_ID", 1)]]
        self.cursor.description = [("column_name",), ("key_sequence",)]
        errors = schema.validate_target_schema_by_category(adapter, self.contract)
        self.assertFalse(any(errors.values()), errors)


if __name__ == "__main__":
    unittest.main()
