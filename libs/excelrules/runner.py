"""Orchestration: resolve source CSV(s), fetch target rows via dynamic SQL,
dispatch to the rule-type validator, and collect per-rule results."""

import re
from pathlib import Path

import pandas as pd

from .models import Rule, RuleResult, Violation
from .validators import validate, where_is_safe

_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _ident(name: "str | None", what: str) -> str:
    if not name or not _IDENT.match(name):
        raise ValueError(f"unsafe {what} identifier: {name!r}")
    return name


_JOIN_SPEC = re.compile(r"^([A-Za-z_]\w*)(?:@([A-Za-z_]\w*)=([A-Za-z_]\w*))?$")


def load_source_frame(rule: Rule, source_dir: "str | Path") -> pd.DataFrame:
    """Each entry in source_tables reads data/samples/<stem>.csv; multiple
    entries left-join into one frame. Two forms per added file:

      - `codes`            -> auto-join on the shared column name (contract
                              convention; errors if no column is shared)
      - `codes@acc=mine`   -> explicit join: accumulated-frame column `acc`
                              equals this file's column `mine`, for sources
                              whose join keys are named differently
                              (e.g. `lab,region_ref@country=ctry`)

    String-typed so key matching is dtype-safe."""
    frames = []
    for entry in rule.source_tables:
        m = _JOIN_SPEC.fullmatch(entry.strip())
        if not m:
            raise ValueError(f"{rule.test_id}: bad source table spec {entry!r}")
        stem, acc_col, file_col = m.groups()
        path = Path(source_dir) / f"{stem}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{rule.test_id}: source file not found: {path}")
        frames.append((pd.read_csv(path, dtype=str), acc_col, file_col))

    df = frames[0][0]
    for other, acc_col, file_col in frames[1:]:
        if acc_col:
            for c, where in ((acc_col, "accumulated"), (file_col, "joined")):
                if c not in (df.columns if where == "accumulated" else other.columns):
                    raise ValueError(f"{rule.test_id}: join column '{c}' not in {where} columns")
            df = df.merge(
                other, left_on=acc_col, right_on=file_col, how="left", suffixes=("", "_j")
            )
        else:
            shared = [c for c in df.columns if c in other.columns]
            if not shared:
                raise ValueError(
                    f"{rule.test_id}: no shared column to join on — use "
                    f"'file@acc_col=file_col' ({list(df.columns)} vs "
                    f"{list(other.columns)})"
                )
            df = df.merge(other, on=shared[0], how="left", suffixes=("", "_j"))
    return df


def target_sql(rule: Rule, schema: str) -> str:
    """Parameterized-shape SELECT for the rule's key + target column, with the
    optional workbook WHERE fragment appended (vetted read-only). For
    CUSTOM_SQL the Logic column is a SQL predicate on target columns and rows
    failing it are the violations — selected server-side as WHERE NOT (logic).
    """
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
    clauses = []
    if rule.where:
        if not where_is_safe(rule.where):
            raise ValueError(f"{rule.test_id}: unsafe WHERE fragment {rule.where!r}")
        clauses.append(f"({rule.where})")
    if rule.rule_type == "CUSTOM_SQL":
        if not rule.logic or not where_is_safe(rule.logic):
            raise ValueError(f"{rule.test_id}: CUSTOM_SQL requires a safe predicate in Logic")
        clauses.append(f"NOT ({rule.logic})")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
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
            tgt = query_fn(target_sql(rule, schema))
            if rule.rule_type == "CUSTOM_SQL":
                # SQL already filtered to failing rows — all fetched = violations
                _, tgt_key = rule.key_pair()
                col = rule.target_column
                vs = [
                    Violation(
                        rule.test_id,
                        col,
                        rule.logic,
                        row.get(col),
                        row.get(tgt_key) if tgt_key else None,
                    )
                    for _, row in tgt.iterrows()
                ]
                results.append(RuleResult(rule, vs))
                continue
            src = load_source_frame(rule, source_dir) if rule.source_tables else pd.DataFrame()
            results.append(RuleResult(rule, validate(rule, src, tgt, mappings)))
        except Exception as e:  # noqa: BLE001 — per-rule isolation by design
            results.append(RuleResult(rule, error=f"{type(e).__name__}: {e}"))
    return results
