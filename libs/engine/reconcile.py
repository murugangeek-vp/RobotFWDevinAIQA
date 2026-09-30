import csv
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from libs.engine.models import ColumnDiff, Contract, ReconResult

_SAFE_FUNCS: dict = {
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "round": lambda v, nd=0: Decimal(str(v)).quantize(
        Decimal(1).scaleb(-int(nd)), rounding=ROUND_HALF_UP
    ),
    "strip": lambda s: str(s).strip(),
}
_FUNC_RE = re.compile(r"^(\w+)\((.*)\)$")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def validate_csv_header(path: str, contract: Contract) -> list:
    """MVP-01: header names/order/count vs contract source.columns."""
    with open(path, newline="", encoding=contract.raw["source"].get("encoding", "utf-8")) as fh:
        reader = csv.reader(fh, delimiter=contract.raw["source"].get("delimiter", ","))
        header = next(reader, [])
    expected = contract.source_columns
    errors = []
    if len(header) != len(expected):
        errors.append(f"header column count: expected {len(expected)}, got {len(header)}")
    for i, (exp, act) in enumerate(zip(expected, header, strict=False)):
        if exp != act:
            errors.append(f"header column {i + 1}: expected '{exp}', got '{act}'")
    return errors


def validate_csv_metadata(path: str, contract: Contract) -> list:
    """MVP-01: encoding decodable, delimiter parses to expected width, row count."""
    meta = contract.metadata
    errors = []
    encoding = meta.get("encoding", "utf-8")
    try:
        with open(path, encoding=encoding) as fh:
            text = fh.read()
    except UnicodeDecodeError as e:
        return [f"file not decodable as {encoding}: {e}"]
    delim = meta.get("delimiter", ",")
    rows = list(csv.reader(text.splitlines(), delimiter=delim))
    if not rows:
        return ["file is empty"]
    width = len(rows[0])
    ragged = [i + 1 for i, r in enumerate(rows[1:], start=1) if len(r) != width]
    if ragged:
        errors.append(f"ragged rows (col count != {width}) at lines {ragged[:5]}")
    expected_count = meta.get("expected_row_count")
    data_rows = len(rows) - 1 if contract.raw["source"].get("header", True) else len(rows)
    if expected_count is not None and data_rows != expected_count:
        errors.append(f"row count: expected {expected_count}, got {data_rows}")
    return errors


def apply_transform(expr: str, row: pd.Series):
    """Evaluate a contract transform: '{a} {b}' template or func(arg, ...) call."""
    expr = expr.strip()
    m = _FUNC_RE.match(expr)
    if m and m.group(1) in _SAFE_FUNCS and "{" not in expr:
        fname, argstr = m.group(1), m.group(2)
        args = []
        for a in argstr.split(","):
            a = a.strip()
            if a in row.index:
                args.append(row[a])
            elif re.fullmatch(r"-?\d+(\.\d+)?", a):
                args.append(Decimal(a))
            else:
                args.append(a.strip("'\""))
        return _SAFE_FUNCS[fname](*args)

    # template: substitute {col} with row values (funcs may wrap placeholders)
    def _sub(match):
        return "" if pd.isna(row.get(match.group(1))) else str(row[match.group(1)])

    rendered = _PLACEHOLDER_RE.sub(_sub, expr)
    m2 = _FUNC_RE.match(rendered)
    if m2 and m2.group(1) in _SAFE_FUNCS:
        fname = m2.group(1)
        args = [a.strip().strip("'\"") for a in m2.group(2).split(",")]
        return _SAFE_FUNCS[fname](*args)
    return rendered


def expected_target_rows(source_df: pd.DataFrame, contract: Contract) -> pd.DataFrame:
    """Derive the expected target dataframe from source data + contract rules."""
    out = {}
    for col in contract.columns:
        if "transform" in col:
            out[col["name"]] = source_df.apply(
                lambda r, c=col: apply_transform(c["transform"], r), axis=1
            )
        else:
            out[col["name"]] = source_df[col["source_name"]]
    return pd.DataFrame(out)


def normalize(v, col: dict):
    """Normalize a value for comparison (whitespace, numeric scale, dates)."""
    if _isna(v):
        return None
    ctype = col["type"]
    if ctype == "integer":
        return int(Decimal(str(v).strip()))
    if ctype == "decimal":
        q = Decimal(1).scaleb(-int(col.get("scale", 2)))
        return Decimal(str(v).strip()).quantize(q, rounding=ROUND_HALF_UP)
    if ctype == "date":
        s = str(v).strip()
        return datetime.strptime(s, col.get("date_format", "%Y-%m-%d")).date()
    if ctype == "timestamp":
        s = str(v).strip()
        for fmt in (col.get("ts_format"), "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            if not fmt:
                continue
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return s
    if ctype == "boolean":
        return str(v).strip().lower() in ("true", "1", "t", "yes")
    return str(v).strip()


def _isna(v) -> bool:
    return (
        v is None
        or (isinstance(v, float) and pd.isna(v))
        or pd.isna(v) is True
        or (isinstance(v, str) and v.strip() == "")
    )


def compare(
    expected_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    contract: Contract,
    columns: "list | None" = None,
) -> ReconResult:
    """MVP-04: key-based record comparison with per-column diffs."""
    keys = contract.keys
    compare_cols = columns or [c["name"] for c in contract.columns]
    colspec = {c["name"]: c for c in contract.columns}
    result = ReconResult(source_count=len(expected_df), target_count=len(actual_df))

    def key_of(row):
        # normalize keys so '1' (CSV str) and 1 (DB int) join correctly
        k = tuple(normalize(row[name], colspec[name]) for name in keys)
        return k[0] if len(k) == 1 else k

    exp_idx = {key_of(r): r for _, r in expected_df.iterrows()}
    act_idx = {key_of(r): r for _, r in actual_df.iterrows()}

    result.missing_in_target = [k for k in exp_idx if k not in act_idx]
    result.extra_in_target = [k for k in act_idx if k not in exp_idx]

    for k in exp_idx.keys() & act_idx.keys():
        erow, arow = exp_idx[k], act_idx[k]
        for cname in compare_cols:
            if cname in keys:
                continue
            e = normalize(erow[cname], colspec[cname])
            a = normalize(arow[cname], colspec[cname])
            if e != a:
                result.diffs.append(ColumnDiff(key=k, column=cname, expected=e, actual=a))
    return result
