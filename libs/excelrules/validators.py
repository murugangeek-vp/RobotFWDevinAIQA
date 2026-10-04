"""Per-rule-type validators. Each takes the source frame (joined when the rule
spans files), the target frame fetched by SQL, and the workbook mappings, and
returns row-level Violations. SQL handles row selection; rule math stays here
so every validator is unit-testable without a database.
"""

import re

import pandas as pd

from libs.engine.reconcile import apply_transform, normalize, parse_map

from .models import Violation


def _key_of(row, src_key, tgt_key):
    return row.get(src_key) if src_key in row.index else row.get(tgt_key)


def _align(rule, source_df, target_df, mappings, expect_fn):
    """Key-match source rows to target rows and compare target_column."""
    src_key, tgt_key = rule.key_pair()
    if not tgt_key:
        raise ValueError(f"{rule.test_id}: Key Column required for {rule.rule_type}")
    if src_key not in source_df.columns:
        raise ValueError(f"{rule.test_id}: key '{src_key}' not in source columns")
    if tgt_key not in target_df.columns:
        raise ValueError(f"{rule.test_id}: key '{tgt_key}' not in target")
    col = rule.target_column
    if col not in target_df.columns:
        raise ValueError(f"{rule.test_id}: column '{col}' not in target")

    expected = source_df.apply(
        lambda r: (r[src_key], expect_fn(r)), axis=1, result_type="reduce"
    )
    # str-keyed: source reads keys as text, the DB returns them typed (int, date)
    exp_map = {str(k): v for k, v in expected}
    coldef = {"type": "string"}
    out = []
    for _, trow in target_df.iterrows():
        key = trow[tgt_key]
        exp = exp_map.get(str(key))
        if str(key) not in exp_map:
            continue  # target row outside this rule's source scope
        act = trow[col]
        if normalize(exp, coldef) != normalize(act, coldef):
            out.append(Violation(rule.test_id, col, exp, act, key))
    return out


def _max_length(rule, source_df, target_df, mappings):
    col = rule.target_column
    if rule.expected is None or not str(rule.expected).isdigit():
        raise ValueError(f"{rule.test_id}: MAX_LENGTH requires an integer Expected value")
    limit = int(rule.expected)
    return [
        Violation(rule.test_id, col, f"len<= {limit}", v, k)
        for k, v in zip(target_df.get(rule.key_pair()[1] or col, []), target_df[col])
        if not pd.isna(v) and len(str(v)) > limit
    ]


def _null_check(rule, source_df, target_df, mappings):
    col = rule.target_column
    _, tgt_key = rule.key_pair()
    return [
        Violation(rule.test_id, col, "not null", v, k)
        for k, v in zip(target_df.get(tgt_key or col, []), target_df[col])
        if pd.isna(v) or str(v).strip() == ""
    ]


def _direct(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(rule, source_df, target_df, mappings, lambda r: r[src])


def _upper(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(rule, source_df, target_df, mappings,
                  lambda r: None if pd.isna(r[src]) else str(r[src]).upper())


def _lower(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(rule, source_df, target_df, mappings,
                  lambda r: None if pd.isna(r[src]) else str(r[src]).lower())


def _concat(rule, source_df, target_df, mappings):
    logic = _normalize_logic(rule.logic)
    return _align(rule, source_df, target_df, mappings,
                  lambda r: apply_transform(logic, r, mappings))


def _map(rule, source_df, target_df, mappings):
    return _align(rule, source_df, target_df, mappings,
                  lambda r: apply_transform(rule.logic, r, mappings))


def _normalize_logic(logic: str) -> str:
    """Accept `a + "|" + b` (spec prose) as well as the `{a}|{b}` template."""
    if "{" in logic or "+" not in logic:
        return logic
    out: list[str] = []
    for seg in logic.split("+"):
        seg = seg.strip()
        if len(seg) >= 2 and seg[0] == seg[-1] and seg[0] in "\"'":
            out.append(seg[1:-1])
        else:
            out.append("{" + seg + "}")
    return "".join(out)


VALIDATORS = {
    "MAX_LENGTH": _max_length,
    "UPPERCASE": _upper,
    "LOWERCASE": _lower,
    "CONCAT": _concat,
    "MAP": _map,
    "DIRECT_COMPARE": _direct,
    "NULL_CHECK": _null_check,
}


_SOURCE_DRIVEN = {"UPPERCASE", "LOWERCASE", "CONCAT", "MAP", "DIRECT_COMPARE"}


def validate(rule, source_df, target_df, mappings) -> list[Violation]:
    if rule.rule_type not in VALIDATORS:
        raise ValueError(f"{rule.test_id}: no validator for {rule.rule_type!r}")
    if rule.rule_type in _SOURCE_DRIVEN:
        missing = set(rule.source_columns) - set(source_df.columns)
        if missing:
            raise ValueError(
                f"{rule.test_id}: source columns not found: {sorted(missing)}"
            )
    return VALIDATORS[rule.rule_type](rule, source_df, target_df, mappings)


def where_is_safe(fragment: "str | None") -> bool:
    """WHERE fragments stay single-statement, read-only expressions. The
    recon_ro session is the real backstop; this only blocks accidental
    multi-statement or mutation syntax."""
    if not fragment:
        return True
    if re.search(r";|--|/\*|\*/", fragment):
        return False
    return not re.search(
        r"\b(drop|delete|insert|update|truncate|alter|create|grant|revoke)\b",
        fragment, re.I)
