"""Per-rule-type validators. Each takes the source frame (joined when the rule
spans files), the target frame fetched by SQL, and the workbook mappings, and
returns row-level Violations. SQL handles row selection; rule math stays here
so every validator is unit-testable without a database.
"""

import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pandas as pd

from libs.engine.reconcile import apply_transform, normalize

from .models import Violation


def _align(rule, source_df, target_df, mappings, expect_fn, actual_fn=None):
    """Key-match source rows to target rows and compare target_column.

    expect_fn(source_row) -> expected value. actual_fn(target_value) ->
    comparable form (used by DECRYPTION, where the target holds ciphertext).
    """
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

    expected = source_df.apply(lambda r: (r[src_key], expect_fn(r)), axis=1, result_type="reduce")
    # str-keyed: source reads keys as text, the DB returns them typed (int, date)
    exp_map = {str(k): v for k, v in expected}
    out = []
    for _, trow in target_df.iterrows():
        key = trow[tgt_key]
        if str(key) not in exp_map:
            continue  # target row outside this rule's source scope
        exp = exp_map[str(key)]
        raw = trow[col]
        act = actual_fn(raw) if actual_fn else raw
        if not _same(exp, act):
            out.append(Violation(rule.test_id, col, exp, raw, key))
    return out


def _same(exp, act) -> bool:
    """Value equality tolerant of numeric representation — Postgres may return
    Decimal('11.0') where the engine computed Decimal('11.00'); 30 == 30.0."""
    if pd.isna(exp) and pd.isna(act):
        return True
    coldef = {"type": "string"}
    if normalize(exp, coldef) == normalize(act, coldef):
        return True
    try:
        return Decimal(str(exp)) == Decimal(str(act))
    except Exception:  # noqa: BLE001 — non-numeric pair, string compare already failed
        return False


def _int_expected(rule) -> int:
    if rule.expected is None or not str(rule.expected).isdigit():
        raise ValueError(f"{rule.test_id}: {rule.rule_type} requires an integer Expected value")
    return int(rule.expected)


def _target_predicate(rule, target_df, test):
    """Shared target-only predicate loop -> Violations for rows failing test."""
    col = rule.target_column
    key_col = rule.key_pair()[1] or col
    return [
        Violation(rule.test_id, col, rule.expected, v, k)
        for k, v in zip(target_df.get(key_col, target_df[col]), target_df[col], strict=False)
        if test(v)
    ]


def _max_length(rule, source_df, target_df, mappings):
    limit = _int_expected(rule)
    return _target_predicate(rule, target_df, lambda v: not pd.isna(v) and len(str(v)) > limit)


def _min_length(rule, source_df, target_df, mappings):
    limit = _int_expected(rule)
    return _target_predicate(rule, target_df, lambda v: not pd.isna(v) and len(str(v)) < limit)


def _null_check(rule, source_df, target_df, mappings):
    return _target_predicate(rule, target_df, lambda v: pd.isna(v) or str(v).strip() == "")


def _regex(rule, source_df, target_df, mappings):
    if not rule.expected:
        raise ValueError(f"{rule.test_id}: REGEX requires a pattern in Expected")
    pat = re.compile(str(rule.expected))
    return _target_predicate(
        rule, target_df, lambda v: not pd.isna(v) and not pat.fullmatch(str(v))
    )


def _date_format(rule, source_df, target_df, mappings):
    if not rule.expected:
        raise ValueError(f"{rule.test_id}: DATE_FORMAT requires a strftime fmt in Expected")

    def bad(v):
        if pd.isna(v):
            return False
        try:
            datetime.strptime(str(v), str(rule.expected))
            return False
        except ValueError:
            return True

    return _target_predicate(rule, target_df, bad)


def _direct(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(rule, source_df, target_df, mappings, lambda r: r[src])


def _upper(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(
        rule,
        source_df,
        target_df,
        mappings,
        lambda r: None if pd.isna(r[src]) else str(r[src]).upper(),
    )


def _lower(rule, source_df, target_df, mappings):
    src = rule.source_columns[0]
    return _align(
        rule,
        source_df,
        target_df,
        mappings,
        lambda r: None if pd.isna(r[src]) else str(r[src]).lower(),
    )


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


def _concat(rule, source_df, target_df, mappings):
    return _expr(rule, source_df, target_df, mappings, logic=_normalize_logic(rule.logic))


def _map(rule, source_df, target_df, mappings):
    return _expr(rule, source_df, target_df, mappings)


def _expr(rule, source_df, target_df, mappings, logic=None):
    """Transform-expression rules: Logic holds an engine expression —
    `upper({c})`, `substr(c,12,4)`, `mask(c)`, `sha256(c)`, `enc(c)`,
    `ifnull(c,'X')`, `casewhen(c,'Y','YES',...)`, `date_fmt(c,fmt)`,
    `round(c,2)`, `mul(a,b)`, `{a}|{b}` templates. LOOKUP normalizes to a
    template over the joined source frame."""
    expr = logic if logic is not None else rule.logic
    if rule.rule_type == "LOOKUP":
        m = re.fullmatch(r"lookup\(\s*\{?(\w+)\}?\s*\)", expr.strip())
        if not m:
            raise ValueError(f"{rule.test_id}: LOOKUP logic must be lookup(<col>)")
        expr = "{" + m.group(1) + "}"
    return _align(
        rule, source_df, target_df, mappings, lambda r: apply_transform(expr, r, mappings)
    )


def _decryption(rule, source_df, target_df, mappings):
    """Inverse check: dec(ciphertext in target) must equal plaintext source."""
    src = rule.source_columns[0]
    return _align(
        rule,
        source_df,
        target_df,
        mappings,
        lambda r: r[src],
        actual_fn=lambda v: apply_transform(f"dec('{v}')", pd.Series(dtype=object)),
    )


def _case_when(rule, source_df, target_df, mappings):
    """CASE_WHEN logic: `case(col) when 'v' then 'r' ... else 'd'`."""
    m = re.match(
        r"^case\((\w+)\)\s*(.*?)\s*else\s+'?([^'\s]+)'?\s*$", rule.logic.strip(), re.I | re.S
    )
    if not m:
        raise ValueError(
            f"{rule.test_id}: CASE_WHEN logic must be " "case(<col>) when 'v' then 'r' ... else 'd'"
        )
    col, body, default = m.group(1), m.group(2), m.group(3)
    pairs = dict(re.findall(r"when\s+'([^']+)'\s+then\s+'([^']+)'", body, re.I))
    if not pairs:
        raise ValueError(f"{rule.test_id}: CASE_WHEN has no when/then pairs")
    return _align(rule, source_df, target_df, mappings, lambda r: pairs.get(str(r[col]), default))


_PY_SAFE = {
    "int": int,
    "float": float,
    "str": str,
    "len": len,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "Decimal": Decimal,
}


def _custom_python(rule, source_df, target_df, mappings):
    """CUSTOM_PYTHON: Logic is a Python expression over source columns —
    evaluated with no builtins beyond a small numeric helper set."""

    def calc(r):
        env = {k: (None if pd.isna(v) else v) for k, v in r.items()}
        return eval(  # noqa: S307 - documented pilot sandbox
            rule.logic, {"__builtins__": {}}, {**_PY_SAFE, **env}
        )

    return _align(rule, source_df, target_df, mappings, calc)


VALIDATORS: dict[str, "Callable[..., list[Violation]]"] = {
    "MAX_LENGTH": _max_length,
    "MIN_LENGTH": _min_length,
    "UPPERCASE": _upper,
    "LOWERCASE": _lower,
    "TRIM": _expr,
    "CONCAT": _concat,
    "MAP": _map,
    "DIRECT_COMPARE": _direct,
    "NULL_CHECK": _null_check,
    "DATE_FORMAT": _date_format,
    "DATE_TRANSFORM": _expr,
    "NUMERIC_ROUND": _expr,
    "DEFAULT_VALUE": _expr,
    "LOOKUP": _expr,
    "REGEX": _regex,
    "MASK": _expr,
    "HASH": _expr,
    "ENCRYPTION": _expr,
    "DECRYPTION": _decryption,
    "SUBSTRING": _expr,
    "PREFIX": _expr,
    "SUFFIX": _expr,
    "CASE_WHEN": _case_when,
    "CUSTOM_PYTHON": _custom_python,
    # CUSTOM_SQL is evaluated SQL-side by the runner (WHERE NOT <logic>).
}

_SOURCE_DRIVEN = {
    "UPPERCASE",
    "LOWERCASE",
    "TRIM",
    "CONCAT",
    "MAP",
    "DIRECT_COMPARE",
    "DATE_TRANSFORM",
    "NUMERIC_ROUND",
    "DEFAULT_VALUE",
    "LOOKUP",
    "MASK",
    "HASH",
    "ENCRYPTION",
    "DECRYPTION",
    "SUBSTRING",
    "PREFIX",
    "SUFFIX",
    "CASE_WHEN",
    "CUSTOM_PYTHON",
}


def validate(rule, source_df, target_df, mappings) -> list[Violation]:
    if rule.rule_type == "CUSTOM_SQL":
        raise ValueError(f"{rule.test_id}: CUSTOM_SQL is evaluated SQL-side")
    if rule.rule_type not in VALIDATORS:
        raise ValueError(f"{rule.test_id}: no validator for {rule.rule_type!r}")
    if rule.rule_type in _SOURCE_DRIVEN:
        missing = set(rule.source_columns) - set(source_df.columns)
        if missing:
            raise ValueError(f"{rule.test_id}: source columns not found: {sorted(missing)}")
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
        r"\b(drop|delete|insert|update|truncate|alter|create|grant|revoke)\b", fragment, re.I
    )
