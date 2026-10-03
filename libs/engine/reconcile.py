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


def _expected_header_for(path: str, contract: Contract) -> "list | None":
    """Expected header for `path`: join files validate against their own
    declared `columns`, everything else against source.columns."""
    import os

    want = os.path.normcase(os.path.abspath(path))
    for j in contract.raw["source"].get("joins") or []:
        if os.path.normcase(os.path.abspath(j["path"])) == want:
            cols = j.get("columns")
            return list(cols) if cols else None
    return contract.source_columns


def validate_csv_header(path: str, contract: Contract, join_index=None) -> list:
    """MVP-01: header names/order/count vs contract — per file, joins included.

    `join_index` forces comparison against joins[i].columns (used to check a
    variant file as the join file, e.g. a defective fixture path).
    """
    with open(path, newline="", encoding=contract.raw["source"].get("encoding", "utf-8")) as fh:
        reader = csv.reader(fh, delimiter=contract.raw["source"].get("delimiter", ","))
        header = next(reader, [])
    if join_index is not None:
        cols = (contract.raw["source"].get("joins") or [])[int(join_index)].get("columns")
        expected = list(cols) if cols else None
    else:
        expected = _expected_header_for(path, contract)
    if expected is None:
        return []  # join file with no declared columns: header unconstrained
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


_MAP_RE = re.compile(r"^map\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)$")


def parse_map(expr: "str | None"):
    """`map(column, mapping_name)` -> (column, mapping_name), else None."""
    m = _MAP_RE.match((expr or "").strip())
    return (m.group(1), m.group(2)) if m else None


def map_code(value, mapping_name: str, mappings: "dict | None"):
    """Translate a source code via a contract mapping; unmapped/empty -> None."""
    if not mappings or mapping_name not in mappings:
        raise ValueError(f"contract has no mapping {mapping_name!r}")
    if _isna(value):
        return None
    return mappings[mapping_name].get(str(value).strip())


def apply_transform(expr: str, row: pd.Series, mappings: "dict | None" = None):
    """Evaluate a contract transform: '{a} {b}' template, func(arg, ...) call,
    or map(column, mapping_name) code translation."""
    expr = expr.strip()
    mapped = parse_map(expr)
    if mapped:
        return map_code(row[mapped[0]], mapped[1], mappings)
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
    mappings = contract.raw.get("mappings")
    for col in contract.columns:
        if "transform" in col:
            out[col["name"]] = source_df.apply(
                lambda r, c=col: apply_transform(c["transform"], r, mappings), axis=1
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
        number = Decimal(str(v).strip())
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("Integer comparison received a non-integral value")
        return int(number)
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

    def index_rows(frame, side):
        index = {}
        for _, row in frame.iterrows():
            if any(_isna(row[name]) for name in keys):
                raise ValueError(f"{side} contains null reconciliation keys")
            key = key_of(row)
            if key in index:
                raise ValueError(f"{side} contains duplicate reconciliation keys")
            index[key] = row
        return index

    exp_idx = index_rows(expected_df, "source")
    act_idx = index_rows(actual_df, "target")

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
