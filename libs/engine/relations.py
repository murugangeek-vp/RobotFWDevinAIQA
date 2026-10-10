"""Referential integrity across mapped tables.

A `Relations` workbook sheet declares FK pairs between mappings (child
column -> parent column, target-side names). The relations stage checks
both sides: orphans in the SOURCE space (a source data-quality problem the
migration inherits) and orphans in the TARGET (a migration integrity
break — e.g. a child row landed but its parent did not).

Null FK values are not orphans (SQL FK semantics) — they are counted
separately. Orphan totals are exact; samples are bounded.
"""

from libs.engine.controls import _gcanon

_SAMPLE_LIMIT = 20


def key_set(chunks, column: str) -> set:
    """Canonical distinct values of `column` across streamed frames."""
    keys = set()
    for chunk in chunks:
        if column not in chunk.columns:
            raise ValueError(f"relation column {column!r} absent from compared frame")
        for v in chunk[column].tolist():
            key = _gcanon(v)
            if key != "":
                keys.add(key)
    return keys


def find_orphans(chunks, column: str, parent_keys: set, sample_limit: int = _SAMPLE_LIMIT):
    """Count child values with no parent key. Returns
    {"orphans": int, "null_fk": int, "samples": [canonical values]}."""
    orphans, null_fk = 0, 0
    samples: list = []
    for chunk in chunks:
        if column not in chunk.columns:
            raise ValueError(f"relation column {column!r} absent from compared frame")
        for v in chunk[column].tolist():
            key = _gcanon(v)
            if key == "":
                null_fk += 1
            elif key not in parent_keys:
                orphans += 1
                if len(samples) < sample_limit:
                    samples.append(key)
    return {"orphans": orphans, "null_fk": null_fk, "samples": samples}
