import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from libs.engine.models import Contract, RuleFailure


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip() == "")


def _check_type(v, ctype: str, col: dict) -> bool:
    s = str(v).strip()
    try:
        if ctype == "integer":
            int(s)
        elif ctype == "decimal":
            Decimal(s)
        elif ctype == "date":
            datetime.strptime(s, col.get("date_format", "%Y-%m-%d"))
        elif ctype == "timestamp":
            datetime.strptime(s, col.get("ts_format", "%Y-%m-%d %H:%M:%S"))
        elif ctype == "boolean":
            if s.lower() not in ("true", "false", "0", "1"):
                return False
        return True
    except (ValueError, InvalidOperation):
        return False


def _record(failures: dict, rule_id: str, column: str, mask: pd.Series, df: pd.DataFrame):
    idx = df.index[mask].tolist()
    if idx:
        key = rule_id if column is None else f"{rule_id}:{column}"
        prev = failures.get(key)
        samples = (df.loc[idx[:5], df.columns[0]].tolist() if df.columns.size else idx[:5])
        if prev:
            prev.failing_rows += len(idx)
            prev.samples.extend(samples[: max(0, 5 - len(prev.samples))])
        else:
            failures[key] = RuleFailure(rule_id=key, column=column or "",
                                        failing_rows=len(idx), samples=samples)


def evaluate_source_rules(df: pd.DataFrame, contract: Contract) -> list:
    """Run contract-driven DQ rules against the raw source dataframe.

    Returns list[RuleFailure]. Columns are only checked when present in the
    source; transform-only (derived) columns skip direct checks but their
    referenced source inputs get implicit not-null checks.
    """
    failures: dict = {}
    source_cols = set(df.columns)

    # implicit not-null on source inputs feeding non-nullable derived columns
    derived_inputs = set()
    for col in contract.columns:
        expr = col.get("transform") or ""
        if not col["nullable"]:
            derived_inputs.update(re.findall(r"\{(\w+)\}", expr))
            m = re.match(r"^\w+\((.+)\)$", expr.strip())
            if m:
                derived_inputs.update(a.strip().strip("'\"") for a in m.group(1).split(",")
                                      if re.fullmatch(r"[A-Za-z_]\w*", a.strip()))
    for name in derived_inputs:
        if name in source_cols:
            _record(failures, "transform_input_not_null", name,
                    df[name].map(_is_empty), df)

    for col in contract.columns:
        src = col.get("source_name")
        if not src or src not in source_cols:
            continue
        series = df[src]
        name = col["name"]
        empty = series.map(_is_empty)

        if not col["nullable"]:
            _record(failures, "not_null", name, empty, df)

        populated = ~empty
        if populated.any():
            if col["type"] in ("integer", "decimal", "date", "timestamp", "boolean"):
                bad = populated & ~series.map(lambda v: _check_type(v, col["type"], col))
                _record(failures, "type", name, bad, df)

            if col["type"] in ("integer", "decimal") and "range" in col:
                lo, hi = col["range"]
                def _in_range(v):
                    if _is_empty(v):
                        return True
                    try:
                        return lo <= Decimal(str(v).strip()) <= hi
                    except InvalidOperation:
                        return True  # caught by type rule
                _record(failures, "range", name, populated & ~series.map(_in_range), df)

            if "max_length" in col:
                too_long = populated & series.map(lambda v: len(str(v)) > col["max_length"])
                _record(failures, "max_length", name, too_long, df)

            if "regex" in col:
                pattern = re.compile(col["regex"])
                bad = populated & ~series.map(lambda v: bool(pattern.match(str(v).strip())))
                _record(failures, "regex", name, bad, df)

            if "allowed_values" in col:
                allowed = {str(v) for v in col["allowed_values"]}
                bad = populated & ~series.map(lambda v: str(v).strip() in allowed)
                _record(failures, "allowed_values", name, bad, df)

        if col.get("unique"):
            dup = populated & series.duplicated(keep=False)
            _record(failures, "unique", name, dup, df)

    return list(failures.values())
