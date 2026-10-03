"""Unit tests for word-boundary truncation (trunc_words) — Case 2 business rule."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.engine import reconcile  # noqa: E402
from libs.engine.models import Contract  # noqa: E402


def _df(rows):
    import pandas as pd

    return pd.DataFrame(rows)


def _contract(rf):
    return Contract(raw={"source": {"row_filter": rf}}, path="t.yaml")


class RowFilterTests(unittest.TestCase):
    DF = None  # built lazily

    def df(self):
        if RowFilterTests.DF is None:
            RowFilterTests.DF = _df(
                [
                    {"id": i, "status": ["active", "inactive", "suspended"][i % 3]}
                    for i in range(1, 101)
                ]
            )
        return RowFilterTests.DF

    def test_in_filter_keeps_matching_only(self):
        out = reconcile.apply_row_filter(
            self.df(), _contract({"column": "status", "in": ["active"]})
        )
        self.assertEqual(len(out), 33)
        self.assertTrue((out["status"] == "active").all())

    def test_multiple_codes_accepted(self):
        out = reconcile.apply_row_filter(
            self.df(), _contract({"column": "status", "in": ["active", "suspended"]})
        )
        self.assertEqual(len(out), 66)

    def test_not_in_excludes(self):
        out = reconcile.apply_row_filter(
            self.df(), _contract({"column": "status", "not_in": ["active"]})
        )
        self.assertEqual(len(out), 67)

    def test_all_and_any_composition(self):
        df = _df(
            [
                {"status": "active", "ccy": "usd"},
                {"status": "active", "ccy": "gbp"},
                {"status": "inactive", "ccy": "usd"},
            ]
        )
        both = reconcile.apply_row_filter(
            df,
            _contract(
                {"all": [{"column": "status", "eq": "active"}, {"column": "ccy", "eq": "usd"}]}
            ),
        )
        either = reconcile.apply_row_filter(
            df,
            _contract(
                {"any": [{"column": "status", "eq": "active"}, {"column": "ccy", "eq": "usd"}]}
            ),
        )
        self.assertEqual(len(both), 1)
        self.assertEqual(len(either), 3)

    def test_zero_match_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "zero source rows"):
            reconcile.apply_row_filter(self.df(), _contract({"column": "status", "eq": "ghost"}))

    def test_allow_empty_permits_zero_rows(self):
        out = reconcile.apply_row_filter(
            self.df(),
            _contract({"column": "status", "eq": "ghost", "allow_empty": True}),
        )
        self.assertEqual(len(out), 0)

    def test_malformed_filters_rejected(self):
        for bad in (
            {"all": [], "any": []},
            {"all": []},
            {"column": "status"},
            {"in": ["active"]},
            {"column": "status", "in": [], "eq": "x"},
            {"column": "status", "in": "active"},
            ["status"],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reconcile.row_filter_predicates(bad)

    def test_stats_shape(self):
        s = reconcile.row_filter_stats(self.df(), _contract({"column": "status", "in": ["active"]}))
        self.assertEqual((s["total"], s["included"], s["excluded"]), (100, 33, 67))


class TruncWordsTests(unittest.TestCase):
    LIMIT = 67

    def test_short_value_unchanged(self):
        self.assertEqual(reconcile._trunc_words("Flat 4", 67), "Flat 4")

    def test_exact_limit_unchanged(self):
        s = "Palm Grove Residency Indiranagar Bengaluru Karnataka India 560038AA"
        self.assertEqual(len(s), 67)
        self.assertEqual(reconcile._trunc_words(s, 67), s)

    def test_mid_word_boundary_drops_whole_last_word(self):
        # 'Kappa' (5 chars) has only 2 chars of budget -> the word is dropped whole
        head = "Rosewood Enclave Phase Two Near Central Mall Avenue Junction XXX"
        s = head + " Kappa Residency Towers"
        self.assertEqual(len(head), 64)
        self.assertEqual(reconcile._trunc_words(s, 67), head)

    def test_word_ending_exactly_at_limit_kept(self):
        exact = "Palm Grove Residency Indiranagar Bengaluru Karnataka India 560038AA"
        self.assertEqual(reconcile._trunc_words(exact + " Extension Block", 67), exact)

    def test_single_long_word_hard_truncated(self):
        s = "SupercalifragilisticexpialidociousavenueblocktwentythreebuildingoneZ"
        self.assertEqual(reconcile._trunc_words(s, 67), s[:67])

    def test_word_starting_at_boundary_dropped_cleanly(self):
        s = "A" * 66 + " Wing"  # space at 66, word starts at 67
        self.assertEqual(reconcile._trunc_words(s, 67), "A" * 66)

    def test_result_never_exceeds_limit(self):
        for s in ["x " * 60, "word " * 30, "  lead", "trail " * 20]:
            self.assertLessEqual(len(reconcile._trunc_words(s, 67)), 67)

    def test_applies_via_transform_expression(self):
        import pandas as pd

        row = pd.Series({"address2": "one two three four five six seven eight nine"})
        out = reconcile.apply_transform("trunc_words(address2, 9)", row)
        self.assertEqual(out, "one two")  # 'three' (5 chars) doesn't fit in 1


if __name__ == "__main__":
    unittest.main()
