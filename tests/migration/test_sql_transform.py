"""Phase-2 transform layer: sqlglot SQL-snippet translator, new safe funcs
(lpad/concat/regexp_match), and declared run params (__load_date)."""

import unittest

import pandas as pd

from libs.engine.mapping_transforms import (
    evaluate,
    mapped_rows,
    parse_expression,
    transform_inputs,
)
from libs.engine.schema import contract_from_dict
from libs.mapping.sql_transform import sql_to_transform


def _eval(expr, row, mappings=None):
    return evaluate(parse_expression(expr), row, mappings or {})


class SqlToTransform(unittest.TestCase):
    def test_concat_pipe_chain(self):
        self.assertEqual(
            sql_to_transform("'CBS'||'CUST'||MAS.CUBK||MAS.CUNBR"),
            "concat('CBS', 'CUST', CUBK, CUNBR)",
        )

    def test_case_when_eq(self):
        self.assertEqual(
            sql_to_transform("CASE WHEN TRIM(CUPERS) = 'N' THEN 'C' ELSE TRIM(CUPERS) END"),
            "casewhen(trim(CUPERS), 'N', 'C', trim(CUPERS))",
        )

    def test_multi_arm_case(self):
        self.assertEqual(
            sql_to_transform(
                "CASE WHEN T = 'P' THEN 'Personal' WHEN T = 'N' THEN 'Non-Personal' ELSE 'Other' END"
            ),
            "casewhen(T, 'P', 'Personal', T, 'N', 'Non-Personal', 'Other')",
        )

    def test_lpad_cast_unwrap(self):
        self.assertEqual(sql_to_transform("LPAD(CUCNCD::VARCHAR, 5, '0')"), "lpad(CUCNCD, 5, '0')")
        self.assertEqual(
            sql_to_transform("LPAD(TRIM(MAS.CUBK::VARCHAR), 3, '0')"),
            "lpad(trim(CUBK), 3, '0')",
        )

    def test_regexp_case(self):
        self.assertEqual(
            sql_to_transform(
                "CASE WHEN REGEXP_LIKE(C, '^[A-Za-z]{2}$') THEN C ELSE 'US' END"
            ),
            "casewhen(regexp_match(C, '^[A-Za-z]{2}$'), 'True', C, 'US')",
        )

    def test_alias_stripped(self):
        self.assertEqual(sql_to_transform("ADDRESS_LINE_2 AS CL_ADDR"), "ADDRESS_LINE_2")

    def test_qualified_column_stripped(self):
        self.assertEqual(sql_to_transform("MAS.CUBK"), "CUBK")

    def test_coalesce(self):
        self.assertEqual(sql_to_transform("COALESCE(CUPERS, 'X')"), "ifnull(CUPERS, 'X')")

    def test_to_date_literal(self):
        self.assertEqual(sql_to_transform("TO_DATE('20251015','YYYYMMDD')"), "'2025-10-15'")

    def test_fail_closed(self):
        for bad in [
            "SELECT * FROM t",
            "x; DROP TABLE t",
            "unknown_fn(a)",
            "a IN (1,2)",
            "CASE WHEN a = 1 THEN 'x' END",  # no ELSE
            "COALESCE(a, b, c)",
            "",
        ]:
            with self.assertRaises(ValueError, msg=bad):
                sql_to_transform(bad)


class NewSafeFuncs(unittest.TestCase):
    def test_lpad(self):
        self.assertEqual(_eval("lpad(C, 5, '0')", {"C": 12}), "00012")
        self.assertEqual(_eval("lpad(C, 2, 'X')", {"C": "ABCD"}), "ABCD")
        self.assertIsNone(_eval("lpad(C, 5, '0')", {"C": None}))

    def test_concat_nulls_strict(self):
        """SQL || propagates NULL — a null key component must produce NULL,
        never a silently-shorter key."""
        self.assertEqual(_eval("concat(A, '-', B)", {"A": "x", "B": "y"}), "x-y")
        self.assertIsNone(_eval("concat(A, B)", {"A": "x", "B": None}))

    def test_regexp_match(self):
        self.assertEqual(_eval("regexp_match(C, '^[A-Z]{2}$')", {"C": "US"}), "True")
        self.assertEqual(_eval("regexp_match(C, '^[A-Z]{2}$')", {"C": "u1"}), "False")
        self.assertEqual(_eval("regexp_match(C, '^[A-Z]{2}$')", {"C": None}), "False")

    def test_casewhen_null_branch_value(self):
        """SQL CASE returns the ELSE arm even when an unmatched THEN value
        is NULL — the null-guard must not swallow the whole expression."""
        expr = "casewhen(regexp_match(C, '^[A-Z]{2}$'), 'True', C, 'US')"
        self.assertEqual(_eval(expr, {"C": None}), "US")
        self.assertEqual(_eval("casewhen(C, 'Y', B, 'DEF')", {"C": "N", "B": None}), "DEF")

    def test_inputs_exclude_literals(self):
        self.assertEqual(
            transform_inputs("concat('CBS', 'CUST', CUBK, CUNBR)", {}),
            {"CUBK", "CUNBR"},
        )


class RunParams(unittest.TestCase):
    def _contract(self, transform):
        return contract_from_dict(
            {
                "contract_name": "t",
                "version": "1",
                "source": {"type": "csv", "path": "x.csv", "columns": ["C"]},
                "target": {"type": "snowflake", "table": "T"},
                "keys": ["K"],
                "columns": [
                    {"name": "K", "type": "string", "nullable": False, "source_name": "C"},
                    {
                        "name": "LOAD_DATE",
                        "type": "string",
                        "nullable": True,
                        "transform": transform,
                    },
                ],
                "params": {"load_date": "2026-10-15"},
            }
        )

    def test_load_date_param_resolves(self):
        c = self._contract("date_fmt(__load_date, '%Y-%m-%d')")
        out = mapped_rows(pd.DataFrame({"C": ["a"]}), c)
        self.assertEqual(out["LOAD_DATE"].iloc[0], "2026-10-15")

    def test_load_date_template(self):
        c = self._contract("{__load_date}")
        out = mapped_rows(pd.DataFrame({"C": ["a"]}), c)
        self.assertEqual(out["LOAD_DATE"].iloc[0], "2026-10-15")


class BuilderParamValidation(unittest.TestCase):
    """__name refs must be declared via env run params — fail closed."""

    def _tm(self, transform):
        from libs.mapping.models import FieldMapping, TableMapping

        return TableMapping(
            mapping_id="m",
            source_schema="S",
            source_table="T",
            target_schema="TG",
            target_table="TT",
            keys=["K"],
            where="",
            batch_size=None,
            enabled=True,
            recon_mode="full",
            fields=[
                FieldMapping(
                    mapping_id="m",
                    source_column="C",
                    target_column="K",
                    target_type="string",
                    nullable=False,
                    transformation="",
                    max_length=None,
                    scale=None,
                    pii=False,
                    enabled=True,
                ),
                FieldMapping(
                    mapping_id="m",
                    source_column="C",
                    target_column="D",
                    target_type="string",
                    nullable=True,
                    transformation=transform,
                    max_length=None,
                    scale=None,
                    pii=False,
                    enabled=True,
                ),
            ],
            controls=[],
            relations=[],
        )

    def test_undeclared_param_fails_closed(self):
        from libs.mapping.builder import build_contract

        with self.assertRaises(ValueError) as ctx:
            build_contract(self._tm("{__load_date}"), {}, {"type": "postgres"}, {})
        self.assertIn("undeclared", str(ctx.exception))

    def test_declared_param_ok_and_not_in_source_columns(self):
        from libs.mapping.builder import build_contract

        c = build_contract(
            self._tm("{__load_date}"),
            {},
            {"type": "postgres"},
            {},
            params={"load_date": "2026-10-15"},
        )
        self.assertEqual(c.raw["params"], {"load_date": "2026-10-15"})
        self.assertNotIn("__load_date", c.raw["source"]["columns"])


if __name__ == "__main__":
    unittest.main()
