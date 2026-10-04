"""Unit tests for the Excel-driven rule engine — parser, validators,
WHERE vetting, and target-SQL generation. All offline (no DB, no xlsx read
beyond synthetic frames)."""

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
    base = dict(
        test_id="T-1",
        rule_type="DIRECT_COMPARE",
        target_table="t",
        target_column="col",
        source_tables=["s"],
        source_columns=["a"],
        key_column="a",
    )
    base.update(kw)
    return Rule(**base)


class ParserTests(unittest.TestCase):
    def test_loads_all_rows(self):
        rules = parser.load_rules(WB)
        self.assertEqual(len(rules), 33)

    def test_all_rule_types_covered(self):
        from libs.excelrules.models import RULE_TYPES

        have = {r.rule_type for r in parser.load_rules(WB)}
        self.assertEqual(have, set(RULE_TYPES))

    def test_enabled_excludes_disabled(self):
        enabled = parser.enabled_rules(WB)
        self.assertEqual(len(enabled), 32)
        self.assertNotIn("ER-008", [r.test_id for r in enabled])

    def test_mappings(self):
        m = parser.load_mappings(WB)
        self.assertEqual(m["products"]["FD"], "FIXED_DEPOSIT")

    def test_key_pair_split(self):
        r = next(r for r in parser.load_rules(WB) if r.test_id == "ER-005")
        self.assertEqual(r.key_pair(), ("acct_seq", "account_seq"))


class ValidatorTests(unittest.TestCase):
    def test_max_length_flags_overlong(self):
        r = _rule(rule_type="MAX_LENGTH", target_column="address2", expected="5", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2], "address2": ["short", "toolong!"]})
        v = validators.validate(r, pd.DataFrame(), tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, 2)

    def test_max_length_requires_expected(self):
        r = _rule(rule_type="MAX_LENGTH", target_column="c", expected=None, key_column="id")
        with self.assertRaises(ValueError):
            validators.validate(r, pd.DataFrame(), pd.DataFrame({"c": ["x"]}), {})

    def test_null_check_flags_blanks(self):
        r = _rule(rule_type="NULL_CHECK", target_column="c", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2, 3], "c": ["x", None, ""]})
        self.assertEqual(len(validators.validate(r, pd.DataFrame(), tgt, {})), 2)

    def test_uppercase_compares_transformed(self):
        r = _rule(rule_type="UPPERCASE", source_columns=["cc"], target_column="cc", key_column="a")
        src = _src({"a": ["1", "2"], "cc": ["gb", "in"]})
        tgt = pd.DataFrame({"a": ["1", "2"], "cc": ["GB", "in"]})
        v = validators.validate(r, src, tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].expected, "IN")

    def test_concat_template_across_join(self):
        r = _rule(
            rule_type="CONCAT",
            source_columns=["branch_code", "acct_seq"],
            target_column="loan_account",
            logic="{branch_code}|{acct_seq}",
            key_column="acct_seq",
        )
        src = _src({"acct_seq": ["11000", "11001"], "branch_code": ["SN", "PR"]})
        tgt = pd.DataFrame(
            {"acct_seq": ["11000", "11001"], "loan_account": ["SN|11000", "XX|11001"]}
        )
        v = validators.validate(r, src, tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].expected, "PR|11001")
        self.assertEqual(v[0].actual, "XX|11001")

    def test_concat_prose_form_normalized(self):
        self.assertEqual(validators._normalize_logic('a + "|" + b'), "{a}|{b}")

    def test_map_resolves_codes(self):
        r = _rule(
            rule_type="MAP",
            source_columns=["p"],
            target_column="pn",
            logic="map(p, products)",
            key_column="a",
        )
        m = {"products": {"LN": "LOAN"}}
        src = _src({"a": ["1", "2"], "p": ["LN", "XX"]})
        tgt = pd.DataFrame({"a": ["1", "2"], "pn": ["LOAN", "LOAN"]})
        v = validators.validate(r, src, tgt, m)
        # row 2: unmapped XX -> expected None vs actual 'LOAN' -> violation
        self.assertEqual(len(v), 1)
        self.assertIsNone(v[0].expected)

    def test_direct_compare(self):
        r = _rule(
            rule_type="DIRECT_COMPARE", source_columns=["s"], target_column="s", key_column="a"
        )
        src = _src({"a": ["1"], "s": ["active"]})
        tgt = pd.DataFrame({"a": ["1"], "s": ["ACTIVE"]})
        self.assertEqual(len(validators.validate(r, src, tgt, {})), 1)


class ExtendedValidatorTests(unittest.TestCase):
    """One focused test per added rule type — synthetic frames, no DB."""

    def test_min_length_flags_short(self):
        r = _rule(rule_type="MIN_LENGTH", target_column="c", expected="4", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2], "c": ["0005", "ab"]})
        v = validators.validate(r, pd.DataFrame(), tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, 2)

    def test_trim(self):
        r = _rule(
            rule_type="TRIM",
            source_columns=["n"],
            target_column="n",
            logic="strip({n})",
            key_column="a",
        )
        src = _src({"a": ["1"], "n": ["  x  "]})
        tgt = pd.DataFrame({"a": ["1"], "n": ["x"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_numeric_round_scale_agnostic(self):
        # PG may return Decimal('11.0') where engine computed Decimal('11.00')
        from decimal import Decimal

        r = _rule(
            rule_type="NUMERIC_ROUND",
            source_columns=["p"],
            target_column="p",
            logic="round(p, 2)",
            key_column="a",
        )
        src = _src({"a": ["1"], "p": ["10.999"]})
        tgt = pd.DataFrame({"a": ["1"], "p": [Decimal("11.0")]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_default_value(self):
        r = _rule(
            rule_type="DEFAULT_VALUE",
            source_columns=["rc"],
            target_column="rd",
            logic="ifnull(rc, 'GLOBAL')",
            key_column="a",
        )
        src = _src({"a": ["1", "2"], "rc": ["R1", ""]})
        tgt = pd.DataFrame({"a": ["1", "2"], "rd": ["R1", "GLOBAL"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_regex_flags_mismatch(self):
        r = _rule(rule_type="REGEX", target_column="c", expected=r"^\d{4}$", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2], "c": ["1234", "12x4"]})
        v = validators.validate(r, pd.DataFrame(), tgt, {})
        self.assertEqual(len(v), 1)

    def test_date_format(self):
        r = _rule(rule_type="DATE_FORMAT", target_column="d", expected="%d/%m/%Y", key_column="id")
        tgt = pd.DataFrame({"id": [1, 2], "d": ["10/01/1985", "1985-01-10"]})
        v = validators.validate(r, pd.DataFrame(), tgt, {})
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].key, 2)

    def test_date_transform(self):
        r = _rule(
            rule_type="DATE_TRANSFORM",
            source_columns=["d"],
            target_column="df",
            logic="date_fmt(d, '%d/%m/%Y')",
            key_column="a",
        )
        src = _src({"a": ["1"], "d": ["1985-01-10"]})
        tgt = pd.DataFrame({"a": ["1"], "df": ["10/01/1985"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_substring_prefix_suffix(self):
        for rt, logic, val in (
            ("SUBSTRING", "substr(c, 12, 4)", "0005"),
            ("PREFIX", "prefix(c, 'AC-')", "AC-P1"),
            ("SUFFIX", "suffix(c, '-ZZ')", "P1-ZZ"),
        ):
            r = _rule(
                rule_type=rt, source_columns=["c"], target_column="t", logic=logic, key_column="a"
            )
            src = _src({"a": ["1"], "c": ["3782822463100005" if rt == "SUBSTRING" else "P1"]})
            tgt = pd.DataFrame({"a": ["1"], "t": [val]})
            self.assertEqual(validators.validate(r, src, tgt, {}), [], rt)

    def test_mask(self):
        r = _rule(
            rule_type="MASK",
            source_columns=["c"],
            target_column="m",
            logic="mask(c)",
            key_column="a",
        )
        src = _src({"a": ["1"], "c": ["4111111111111111"]})
        tgt = pd.DataFrame({"a": ["1"], "m": ["****1111"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_hash(self):
        import hashlib

        r = _rule(
            rule_type="HASH",
            source_columns=["s"],
            target_column="h",
            logic="sha256(s)",
            key_column="a",
        )
        src = _src({"a": ["1"], "s": ["123456789"]})
        tgt = pd.DataFrame({"a": ["1"], "h": [hashlib.sha256(b"123456789").hexdigest()]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_encrypt_decrypt_roundtrip(self):
        enc = _rule(
            rule_type="ENCRYPTION",
            source_columns=["p"],
            target_column="e",
            logic="enc(p)",
            key_column="a",
        )
        dec = _rule(rule_type="DECRYPTION", source_columns=["p"], target_column="e", key_column="a")
        from libs.engine.reconcile import apply_transform

        cipher = apply_transform("enc(p)", pd.Series({"p": "1234"}))
        src = _src({"a": ["1"], "p": ["1234"]})
        tgt = pd.DataFrame({"a": ["1"], "e": [cipher]})
        self.assertEqual(validators.validate(enc, src, tgt, {}), [])
        self.assertEqual(validators.validate(dec, src, tgt, {}), [])
        bad = pd.DataFrame({"a": ["1"], "e": ["00ff"]})
        self.assertEqual(len(validators.validate(dec, src, bad, {})), 1)

    def test_case_when(self):
        r = _rule(
            rule_type="CASE_WHEN",
            source_columns=["f"],
            target_column="fl",
            key_column="a",
            logic="case(f) when 'Y' then 'YES' when 'N' then 'NO' else 'OTHER'",
        )
        src = _src({"a": ["1", "2", "3"], "f": ["Y", "N", "X"]})
        tgt = pd.DataFrame({"a": ["1", "2", "3"], "fl": ["YES", "NO", "OTHER"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])
        tgt.loc[2, "fl"] = "WRONG"
        self.assertEqual(len(validators.validate(r, src, tgt, {})), 1)

    def test_lookup_template(self):
        r = _rule(
            rule_type="LOOKUP",
            source_columns=["country"],
            target_column="region",
            logic="lookup(region)",
            key_column="a",
        )
        src = _src({"a": ["1"], "country": ["gb"], "region": ["EMEA"]})
        tgt = pd.DataFrame({"a": ["1"], "region": ["EMEA"]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_custom_python(self):
        r = _rule(
            rule_type="CUSTOM_PYTHON",
            source_columns=["q", "s"],
            target_column="lt",
            logic="int(q) * int(s)",
            key_column="a",
        )
        src = _src({"a": ["1"], "q": ["3"], "s": ["10"]})
        tgt = pd.DataFrame({"a": ["1"], "lt": [30]})
        self.assertEqual(validators.validate(r, src, tgt, {}), [])

    def test_custom_sql_sql_side(self):
        r = _rule(rule_type="CUSTOM_SQL", target_column="m", logic="m LIKE '****%'", key_column="a")
        sql = runner.target_sql(r, "public")
        self.assertIn("NOT (m LIKE '****%')", sql)
        # rows the server returns ARE violations (predicate already applied)
        res = runner.run_rules([r], {}, ".", lambda _s: pd.DataFrame({"a": [9], "m": ["bad-mask"]}))
        self.assertEqual(res[0].status, "FAIL")
        self.assertEqual(len(res[0].violations), 1)


class SafetyTests(unittest.TestCase):
    def test_where_vetting(self):
        self.assertTrue(validators.where_is_safe("status = 'active'"))
        self.assertTrue(validators.where_is_safe(None))
        for bad in ("1=1; DROP TABLE t", "x -- comment", "id=1 /* x */", "id IN (DELETE FROM t)"):
            self.assertFalse(validators.where_is_safe(bad), bad)

    def test_target_sql_builds_and_vets(self):
        r = _rule(
            key_column="a=k",
            where="status = 'active'",
            target_table="customer",
            target_column="country",
        )
        sql = runner.target_sql(r, "public")
        self.assertEqual(sql, "SELECT k, country FROM public.customer " "WHERE (status = 'active')")
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
