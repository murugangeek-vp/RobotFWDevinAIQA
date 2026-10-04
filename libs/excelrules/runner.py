"""Orchestration: resolve source CSV(s), fetch target rows via dynamic SQL,
dispatch to the rule-type validator, and collect per-rule results."""

import re
from pathlib import Path

import pandas as pd

from .models import Rule, RuleResult
from .validators import validate, where_is_safe

_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _ident(name: "str | None", what: str) -> str:
    if not name or not _IDENT.match(name):
        raise ValueError(f"unsafe {what} identifier: {name!r}")
    return name


def load_source_frame(rule: Rule, source_dir: "str | Path") -> pd.DataFrame:
    """Each entry in source_tables reads data/samples/<stem>.csv; multiple
    entries left-join on their shared key column (same convention as contract
    joins). String-typed so key matching is dtype-safe."""
    frames = []
    for stem in rule.source_tables:
        path = Path(source_dir) / f"{stem}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{rule.test_id}: source file not found: {path}")
        frames.append(pd.read_csv(path, dtype=str))
    df = frames[0]
    for other in frames[1:]:
        shared = [c for c in df.columns if c in other.columns]
        if not shared:
            raise ValueError(
                f"{rule.test_id}: no shared column to join on "
                f"({list(df.columns)} vs {list(other.columns)})"
            )
        df = df.merge(other, on=shared[0], how="left", suffixes=("", "_j"))
    return df


def target_sql(rule: Rule, schema: str) -> str:
    """Parameterized-shape SELECT for the rule's key + target column, with the
    optional workbook WHERE fragment appended (vetted read-only)."""
    _, tgt_key = rule.key_pair()
    cols = []
    for c in ([tgt_key] if tgt_key else []) + [rule.target_column]:
        c = _ident(c, "column")
        if c not in cols:
            cols.append(c)
    sql = (
        f"SELECT {', '.join(cols)} FROM "
        f"{_ident(schema, 'schema')}.{_ident(rule.target_table, 'table')}"
    )
    if rule.where:
        if not where_is_safe(rule.where):
            raise ValueError(f"{rule.test_id}: unsafe WHERE fragment {rule.where!r}")
        sql += f" WHERE {rule.where}"
    return sql


def run_rules(
    rules: list[Rule],
    mappings: dict,
    source_dir: "str | Path",
    query_fn,
    schema: str = "public",
) -> list[RuleResult]:
    """Run every rule in order; a broken rule becomes ERROR, never a crash —
    one bad Excel row must not hide the other 999 results."""
    results = []
    for rule in rules:
        try:
            src = load_source_frame(rule, source_dir) if rule.source_tables else pd.DataFrame()
            tgt = query_fn(target_sql(rule, schema))
            results.append(RuleResult(rule, validate(rule, src, tgt, mappings)))
        except Exception as e:  # noqa: BLE001 — per-rule isolation by design
            results.append(RuleResult(rule, error=f"{type(e).__name__}: {e}"))
    return results
