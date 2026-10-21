"""Column-level data profiling for source-vs-target comparison.

Recon proves row-by-row equality; profiling catches defects recon can miss
on sampled or hash-mode comparisons — distribution shift (truncated values,
wrong code page, silent casts), null-rate drift, and min/max range drift.
Distinct sets are capped so cardinality never grows unbounded in memory.
"""

from __future__ import annotations

_DISTINCT_CAP = 50_000


def _accumulate(state, col, series):
    st = state.setdefault(
        col,
        {"nulls": 0, "distinct": set(), "capped": False, "min": None, "max": None},
    )
    st["nulls"] += int(series.isna().sum())
    vals = series.dropna()
    if not vals.empty:
        if not st["capped"]:
            st["distinct"].update(vals.astype(str).unique())
            if len(st["distinct"]) >= _DISTINCT_CAP:
                st["capped"] = True
        lo, hi = vals.min(), vals.max()
        if st["min"] is None or (lo is not None and lo < st["min"]):
            st["min"] = lo
        if st["max"] is None or (hi is not None and hi > st["max"]):
            st["max"] = hi


def profile_chunks(chunks):
    """Accumulate nulls/distinct/min/max/rows per column across chunks."""
    state = {}
    rows = 0
    for chunk in chunks:
        rows += len(chunk)
        for col in chunk.columns:
            _accumulate(state, col, chunk[col])
    return rows, state


def compare_profiles(src_rows, src, tgt_rows, tgt):
    """Return a bounded list of drift findings between two column profiles."""
    diffs = []
    for col in sorted(set(src) | set(tgt)):
        s, t = src.get(col), tgt.get(col)
        if s is None:
            diffs.append({"column": col, "issue": "missing in source profile"})
            continue
        if t is None:
            diffs.append({"column": col, "issue": "missing in target profile"})
            continue
        checks = (
            ("nulls", s["nulls"], t["nulls"]),
            ("min", str(s["min"]), str(t["min"])),
            ("max", str(s["max"]), str(t["max"])),
        )
        for stat, sv, tv in checks:
            if sv != tv:
                diffs.append({"column": col, "stat": stat, "source": sv, "target": tv})
        if s["capped"] and t["capped"]:
            diffs.append(
                {
                    "column": col,
                    "stat": "distinct",
                    "source": f">={_DISTINCT_CAP}",
                    "target": f">={_DISTINCT_CAP}",
                    "issue": "both sides exceed distinct cap — equality unverifiable "
                    "in memory; needs SQL COUNT(DISTINCT) pushdown",
                }
            )
        elif s["capped"] != t["capped"] or len(s["distinct"]) != len(t["distinct"]):
            diffs.append(
                {
                    "column": col,
                    "stat": "distinct",
                    "source": len(s["distinct"]),
                    "target": len(t["distinct"]),
                }
            )
    if src_rows != tgt_rows:
        diffs.append({"column": "*", "stat": "rows", "source": src_rows, "target": tgt_rows})
    return diffs[:200]
