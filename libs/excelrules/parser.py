"""Parse migration_rules.xlsx into Rule objects and mapping tables.

Fail-closed: an unknown rule type, missing required column, duplicate Test ID,
or absent sheet aborts the load — a silently skipped rule would look like a
pass. Enabled=no rows are parsed but not returned by `enabled_rules`.
"""

from pathlib import Path

import pandas as pd

from .models import RULE_TYPES, Rule

RULES_SHEET = "Rules"
MAPPING_SHEET = "Mapping"

# normalized-header -> Rule field. Extra columns are ignored; "Key Column" is
# the one extension beyond the client's spec, needed to name violating rows.
_RULE_COLS = {
    "test id": "test_id",
    "rule type": "rule_type",
    "source table": "source_table",
    "source column(s)": "source_columns",
    "source columns": "source_columns",
    "target table": "target_table",
    "target column": "target_column",
    "transformation logic": "logic",
    "logic": "logic",
    "expected": "expected",
    "severity": "severity",
    "where clause": "where",
    "enabled": "enabled",
    "key column": "key_column",
}
_MAP_COLS = {
    "mapping name": "mapping_name",
    "source value": "source_value",
    "target value": "target_value",
}
_REQUIRED = ("test_id", "rule_type", "target_table", "target_column")

_TRUE = {"yes", "y", "true", "1", "enabled"}
_FALSE = {"no", "n", "false", "0", "disabled"}


def _norm_headers(df: pd.DataFrame, sheet: str, colmap: dict) -> pd.DataFrame:
    rename = {
        c: colmap[str(c).strip().lower()] for c in df.columns if str(c).strip().lower() in colmap
    }
    if not rename:
        raise ValueError(f"{sheet} sheet: no recognizable columns in {list(df.columns)}")
    return df.rename(columns=rename)


def _split_cell(v) -> list[str]:
    return [p.strip() for p in str(v or "").split(",") if p.strip()]


def _enabled(v) -> bool:
    s = str(v if v is not None else "").strip().lower()
    # _TRUE / blank / unrecognized -> on (fail open is safer than silently skipping)
    return s not in _FALSE


def load_rules(path: "str | Path") -> list[Rule]:
    """All Rules-sheet rows -> Rule objects (enabled and disabled)."""
    df = pd.read_excel(path, sheet_name=RULES_SHEET, dtype=str).fillna("")
    df = _norm_headers(df, RULES_SHEET, _RULE_COLS)
    for req in _REQUIRED:
        if req not in df.columns:
            raise ValueError(f"{RULES_SHEET} sheet: missing required column '{req}'")

    rules, seen = [], set()
    for _i, row in df.iterrows():
        if not str(row.get("test_id", "")).strip():
            continue  # blank spacer row
        raw = {f: str(row.get(f, "")).strip() or None for f in df.columns}
        rid = raw.get("test_id")
        if not rid:
            continue
        if rid in seen:
            raise ValueError(f"{RULES_SHEET} sheet: duplicate Test ID '{rid}'")
        seen.add(rid)
        rtype = (raw.get("rule_type") or "").strip().upper()
        if rtype not in RULE_TYPES:
            raise ValueError(f"{rid}: unknown rule type {rtype!r}; expected one of {RULE_TYPES}")
        tt, tc = raw.get("target_table"), raw.get("target_column")
        if not tt or not tc:
            raise ValueError(f"{rid}: Target Table and Target Column are required")
        rules.append(
            Rule(
                test_id=rid,
                rule_type=rtype,
                target_table=tt,
                target_column=tc,
                source_tables=_split_cell(raw.get("source_table")),
                source_columns=_split_cell(raw.get("source_columns")),
                logic=raw.get("logic") or "",
                expected=raw.get("expected"),
                severity=(raw.get("severity") or "HIGH").upper(),
                where=raw.get("where"),
                key_column=raw.get("key_column"),
                enabled=_enabled(row.get("enabled")),
            )
        )
    return rules


def enabled_rules(path: "str | Path") -> list[Rule]:
    return [r for r in load_rules(path) if r.enabled]


def load_mappings(path: "str | Path") -> dict:
    """Mapping sheet -> {mapping_name: {source_value: target_value}}."""
    try:
        df = pd.read_excel(path, sheet_name=MAPPING_SHEET, dtype=str).fillna("")
    except ValueError:
        return {}
    df = _norm_headers(df, MAPPING_SHEET, _MAP_COLS)
    mappings: dict[str, dict] = {}
    for _, row in df.iterrows():
        name = str(row.get("mapping_name", "")).strip()
        if not name:
            continue
        mappings.setdefault(name, {})[str(row.get("source_value", "")).strip()] = str(
            row.get("target_value", "")
        ).strip()
    return mappings
