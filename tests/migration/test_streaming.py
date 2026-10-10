import unittest

import pandas as pd

from libs.engine.models import Contract
from libs.engine.reconcile import compare_chunked, hash_compare


def contract():
    return Contract(
        {
            "keys": ["id"],
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "a", "type": "string"},
                {"name": "b", "type": "string"},
            ],
        },
        "test",
    )


def frames(rows):
    return (pd.DataFrame([row], columns=["id", "a", "b"]) for row in rows)


class StreamingTests(unittest.TestCase):
    def test_duplicates_across_chunk_boundaries(self):
        for fn in (compare_chunked, hash_compare):
            for side in ("source", "target"):
                with self.subTest(fn=fn.__name__, side=side):
                    good = [(1, "a", "b")]
                    bad = good * 2
                    with self.assertRaisesRegex(ValueError, "duplicate"):
                        fn(
                            frames(bad if side == "source" else good),
                            frames(bad if side == "target" else good),
                            contract(),
                        )

    def test_null_keys_rejected(self):
        for fn in (compare_chunked, hash_compare):
            with self.assertRaisesRegex(ValueError, "null"):
                fn(frames([(None, "a", "b")]), frames([]), contract())

    def test_hash_serialization_is_unambiguous(self):
        result = hash_compare(frames([(1, "x|y", "z")]), frames([(1, "x", "y|z")]), contract())
        self.assertEqual(result.mismatch_count, 2)
        self.assertEqual({d.column for d in result.diffs}, {"a", "b"})

    def test_missing_extra_and_column_diff(self):
        for fn in (compare_chunked, hash_compare):
            result = fn(
                frames([(1, "a", "b"), (2, "c", "d")]),
                frames([(2, "X", "d"), (3, "e", "f")]),
                contract(),
            )
            self.assertEqual(result.mismatch_count, 3)
            self.assertEqual(result.missing_in_target, [1])
            self.assertEqual(result.extra_in_target, [3])

    def test_exact_totals_with_bounded_samples(self):
        result = compare_chunked(
            frames([(i, "a", "b") for i in range(1, 5)]), frames([]), contract(), sample_limit=1
        )
        self.assertEqual(result.mismatch_count, 4)
        self.assertEqual(len(result.missing_in_target), 1)

    def test_out_of_order_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "order"):
            compare_chunked(frames([(2, "a", "b"), (1, "a", "b")]), frames([]), contract())


if __name__ == "__main__":
    unittest.main()
