"""Unit tests for the Excel-driven rule engine — parser, validators,
WHERE vetting, and target-SQL generation. All offline (no DB, no xlsx read
beyond synthetic frames)."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from libs.excelrules import parser, runner, validators
from libs.excelrules.models import Rule

DATA = Path(__file__).resolve().parent.parent.parent / "data" / "rules"
WB = DATA / "migration_rules.xlsx"


def _src(rows):
    return pd.DataFrame(rows)


def _rule(**kw):
    base = dict(test_id="T-1", rule_type="DIRECT_COMPARE", target_table="t",
                target_column="col", source_tables=["s"], source_columns=["a"],
                key_column="a")
    base.update(kw)
    return Rule(**base)


class ParserTests(unittest.TestCase):
    def test_loads_all_rows(self):
        rules = parser.load_rules(WB)
        self.assertEqual(len(rules), 9)

    def test_enabled_excludes_disabled(self):
        enabled = parser.enabled_rules(WB)
        self.assertEqual(len(enabled), 8)
        self.assertNotIn("ER-008", [r.test_id for r in enabled])

    def test_mappings(self):
        m = parser.load_mappings(WB)
        self.assertEqual(m["products"]["FD"], "FIXED_DEPOSIT")

    def test_key_pair_split(self):
        r = next(r for r in parser.load_rules(WB) if r.test_id == "ER-005")
        self.assertEqual(r.key_pair(), ("acct_seq", "account_seq"))


class ValidatorTests(unittest.TestCase):
    def test_max_length_flags_overlong(self):
        r = _rule(rule_type="MAX_LENGTH", target_column="address2",
                  expected="5", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2], "address2": ["short", "toolong!"]})
        v = validators.validate(r, pd.DataFrame(), tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, 2)

    def test_max_length_requires_expected(self):
        r = _rule(rule_type="MAX_LENGTH", target_column="c", expected=None,
                  key_column="id")
        with self.assertRaises(ValueError):
            validators.validate(r, pd.DataFrame(), pd.DataFrame({"c": ["x"]}), {})

    def test_null_check_flags_blanks(self):
        r = _rule(rule_type="NULL_CHECK", target_column="c", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2, 3], "c": ["x", None, ""]})
        self.assertEqual(len(validators.validate(r, pd.DataFrame(), tgt, {})), 2)

    def test_uppercase_compares_transformed(self):
        r = _rule(rule_type="UPPERCASE", source_columns=["cc"], target_column="cc",
                  key_column="a")
        src = _src({"a": ["1", "2"], "cc": ["gb", "in"]})
        tgt = pd.DataFrame({"a": ["1", "2"], "cc": ["GB", "in"]})
        v = validators.validate(r, src, tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].expected, "IN")

    def test_concat_template_across_join(self):
        r = _rule(rule_type="CONCAT", source_columns=["branch_code", "acct_seq"],
                  target_column="loan_account", logic="{branch_code}|{acct_seq}",
                  key_column="acct_seq")
        src = _src({"acct_seq": ["11000", "11001"], "branch_code": ["SN", "PR"]})
        tgt = pd.DataFrame({"acct_seq": ["11000", "11001"],
                            "loan_account": ["SN|11000", "XX|11001"]})
        v = validators.validate(r, src, tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].expected, "PR|11001")
        self.assertEqual(v[0].actual, "XX|11001")

    def test_concat_prose_form_normalized(self):
        self.assertEqual(validators._normalize_logic('a + "|" + b'), "{a}|{b}")

    def test_map_resolves_codes(self):
        r = _rule(rule_type="MAP", source_columns=["p"], target_column="pn",
                  logic="map(p, products)", key_column="a")
        m = {"products": {"LN": "LOAN"}}
        src = _src({"a": ["1", "2"], "p": ["LN", "XX"]})
        tgt = pd.DataFrame({"a": ["1", "2"], "pn": ["LOAN", "LOAN"]})
        v = validators.validate(r, src, tgt, m)
        # row 2: unmapped XX -> expected None vs actual 'LOAN' -> violation
        self.assertEqual(len(v), 1)
        self.assertIsNone(v[0].expected)

    def test_direct_compare(self):
        r = _rule(rule_type="DIRECT_COMPARE", source_columns=["s"],
                  target_column="s", key_column="a")
        src = _src({"a": ["1"], "s": ["active"]})
        tgt = pd.DataFrame({"a": ["1"], "s": ["ACTIVE"]})
        self.assertEqual(len(validators.validate(r, src, tgt, {})), 1)


class SafetyTests(unittest.TestCase):
    def test_where_vetting(self):
        self.assertTrue(validators.where_is_safe("status = 'active'"))
        self.assertTrue(validators.where_is_safe(None))
        for bad in ("1=1; DROP TABLE t", "x -- comment", "id=1 /* x */",
                    "id IN (DELETE FROM t)"):
            self.assertFalse(validators.where_is_safe(bad), bad)

    def test_target_sql_builds_and_vets(self):
        r = _rule(key_column="a=k", where="status = 'active'",
                  target_table="customer", target_column="country")
        sql = runner.target_sql(r, "public")
        self.assertEqual(sql, "SELECT k, country FROM public.customer "
                              "WHERE status = 'active'")
        bad = _rule(target_table="x; drop", target_column="c")
        with self.assertRaises(ValueError):
            runner.target_sql(bad, "public")

    def test_source_join_on_shared_key(self):
        r = _rule(source_tables=["account_extract", "account_codes"])
        df = runner.load_source_frame(r, DATA.parent / "samples")
        self.assertIn("branch_code", df.columns)
        self.assertIn("prod_code", df.columns)


if __name__ == "__main__":
    unittest.main()
