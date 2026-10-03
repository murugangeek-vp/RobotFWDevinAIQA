import csv
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from libs.engine.models import ColumnDiff, Contract, ReconResult


def _trunc_words(v, limit) -> str:
    """Word-boundary truncation: keep only whole words within `limit` chars.

    If the limit lands mid-word the partial last word is dropped entirely —
    e.g. a 5-char word with 2 chars of budget is removed, not cut. A single
    word longer than the limit (no space to break on) is hard-truncated.
    """
    s = str(v)
    n = int(limit)
    if len(s) <= n:
        return s
    cut = s[:n]
    if s[n] != " " and cut[-1] != " ":  # boundary landed mid-word
        i = cut.rfind(" ")
        if i >= 0:
            cut = cut[:i]
    return cut.rstrip()


_SAFE_FUNCS: dict = {
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "round": lambda v, nd=0: Decimal(str(v)).quantize(
        Decimal(1).scaleb(-int(nd)), rounding=ROUND_HALF_UP
    ),
    "strip": lambda s: str(s).strip(),
    "trunc_words": _trunc_words,
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
    """Pilot-01: header names/order/count vs contract — per file, joins included.

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
    """Pilot-01: encoding decodable, delimiter parses to expected width, row count."""
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


_FILTER_OPS = ("in", "not_in", "eq", "ne")


def row_filter_predicates(rf) -> "tuple[str, list]":
    """Normalize contract source.row_filter -> (mode, [predicate, ...]).

    Forms: a single predicate {column, <op>} or {all: [...]} / {any: [...]}.
    Optional sibling key allow_empty lets a zero-row result pass validation
    (still a hard error at filter time unless set — see apply_row_filter).
    Fails closed on any malformed shape.
    """
    if not isinstance(rf, dict):
        raise ValueError("source.row_filter must be a mapping")
    wrapper = [k for k in ("all", "any") if k in rf]
    if len(wrapper) > 1:
        raise ValueError("row_filter cannot mix 'all' and 'any'")
    if wrapper:
        mode = wrapper[0]
        unknown = set(rf) - {mode, "allow_empty"}
        if unknown:
            raise ValueError(f"row_filter has unknown keys: {sorted(unknown)}")
        preds = rf[mode]
        if not isinstance(preds, list) or not preds:
            raise ValueError(f"row_filter.{mode} must be a non-empty list")
    else:
        mode, preds = "all", [rf]
    for p in preds:
        if not isinstance(p, dict):
            raise ValueError("row_filter predicates must be mappings")
        ops = [o for o in _FILTER_OPS if o in p]
        if "column" not in p or len(ops) != 1:
            raise ValueError(
                f"row_filter predicate needs 'column' plus exactly one of {_FILTER_OPS}: {p!r}"
            )
        if ops[0] in ("in", "not_in") and (not isinstance(p[ops[0]], list) or not p[ops[0]]):
            raise ValueError(f"row_filter '{ops[0]}' needs a non-empty value list: {p!r}")
    return mode, preds


def _filter_mask(df: pd.DataFrame, pred: dict):
    col = pred["column"]
    if col not in df.columns:
        raise ValueError(f"row_filter column {col!r} not in source columns {sorted(df.columns)}")
    vals = df[col].astype(str).str.strip()
    if "in" in pred:
        return vals.isin([str(v) for v in pred["in"]])
    if "not_in" in pred:
        return ~vals.isin([str(v) for v in pred["not_in"]])
    if "eq" in pred:
        return vals == str(pred["eq"])
    return vals != str(pred["ne"])


def apply_row_filter(source_df: pd.DataFrame, contract: Contract) -> pd.DataFrame:
    """Contract source.row_filter — the migration scope. Rows outside the
    filter are out of scope, not defects: they are excluded from expected
    target rows AND flagged as extras if they appear in the target."""
    rf = (contract.raw.get("source") or {}).get("row_filter")
    if rf is None:
        return source_df
    mode, preds = row_filter_predicates(rf)
    mask = _filter_mask(source_df, preds[0])
    for p in preds[1:]:
        m = _filter_mask(source_df, p)
        mask = mask & m if mode == "all" else mask | m
    out = source_df[mask]
    if len(out) == 0 and not rf.get("allow_empty"):
        raise ValueError(
            "row_filter matched zero source rows — check the filter or set allow_empty: true"
        )
    return out


def row_filter_stats(source_df: pd.DataFrame, contract: Contract) -> dict:
    """{total, included, excluded, filter} scope accounting for reporting."""
    rf = (contract.raw.get("source") or {}).get("row_filter")
    total = len(source_df)
    if rf is None:
        return {"total": total, "included": total, "excluded": 0, "filter": None}
    included = len(apply_row_filter(source_df, contract))
    return {"total": total, "included": included, "excluded": total - included, "filter": rf}


def expected_target_rows(source_df: pd.DataFrame, contract: Contract) -> pd.DataFrame:
    """Derive the expected target dataframe from source data + contract rules."""
    source_df = apply_row_filter(source_df, contract)
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
    """Pilot-04: key-based record comparison with per-column diffs."""
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
