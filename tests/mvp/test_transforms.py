"""Unit tests for word-boundary truncation (trunc_words) — Case 2 business rule."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.engine import reconcile  # noqa: E402


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
