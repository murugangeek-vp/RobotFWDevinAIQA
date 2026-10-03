"""MIG-04: control totals (count/sum/min/max/null_count/count_distinct, optional group-by).

Source values are computed on the *expected* target rows (contract transforms applied)
with exact Decimal arithmetic; target values are aggregated inside the target database
(TargetAdapter.aggregate) so no target rows are transferred. Both sides are normalized
with the contract column spec before comparison, so 100.10 and Decimal('100.1') match
and float rounding can never hide a difference.
"""

import re

from libs.engine.reconcile import _isna, normalize

MAX_GROUPS = 10000
_MAP_RE = re.compile(r"^map\(\s*(\w+)\s*,\s*(\w+)\s*\)$")


def _colspec(contract, name):
    return contract.column(name)


def _norm_group(contract, group_by, values) -> tuple:
    return tuple(
        None if _isna(v) else normalize(v, _colspec(contract, g))
        for g, v in zip(group_by, values, strict=True)
    )


def _norm_value(contract, control, v):
    if control["type"] in ("count", "null_count", "count_distinct"):
        return int(v or 0)
    if _isna(v):
        return None
    return normalize(v, _colspec(contract, control["column"]))


def source_values(expected_df, contract, control) -> dict:
    """{group tuple: value} computed from expected target rows."""
    group_by = control.get("group_by") or []
    ctype, column = control["type"], control.get("column")
    buckets: dict = {}
    cols = [*group_by, *([column] if column else [])]
    # a zero-column frame yields no tuples, so ungrouped counts iterate row positions
    rows = expected_df[cols].itertuples(index=False, name=None) if cols else [()] * len(expected_df)
    for row in rows:
        group = _norm_group(contract, group_by, row[: len(group_by)])
        bucket = buckets.setdefault(group, [])
        bucket.append(row[len(group_by)] if column else None)
        if len(buckets) > MAX_GROUPS:
            raise ValueError(f"control {control['id']!r} exceeds {MAX_GROUPS} groups")
    if not group_by and not buckets:
        buckets[()] = []
    out: dict = {}
    for group, raw in buckets.items():
        if ctype == "count":
            out[group] = len(raw)
            continue
        spec = _colspec(contract, column)
        populated = [normalize(v, spec) for v in raw if not _isna(v)]
        if ctype == "null_count":
            out[group] = len(raw) - len(populated)
        elif ctype == "count_distinct":
            out[group] = len(set(populated))
        elif not populated:
            out[group] = None
        elif ctype == "sum":
            total = sum(populated[1:], populated[0])
            out[group] = normalize(total, spec)
        elif ctype == "min":
            out[group] = min(populated)
        else:
            out[group] = max(populated)
    return out


def target_values(adapter, contract, control) -> dict:
    group_by = control.get("group_by") or []
    rows = adapter.aggregate(contract.table, control["type"], control.get("column"), group_by)
    out = {}
    for row in rows:
        group = _norm_group(contract, group_by, row[: len(group_by)])
        out[group] = _norm_value(contract, control, row[len(group_by)])
    if not group_by and not out:
        out[()] = _norm_value(contract, control, None)
    return out


def compare_control(expected: dict, actual: dict, control) -> list:
    """[{control, group, expected, actual}] for every group that differs."""
    diffs = []
    for group in sorted(expected.keys() | actual.keys(), key=lambda g: tuple(map(str, g))):
        e, a = expected.get(group), actual.get(group)
        if e != a:
            diffs.append({"control": control["id"], "group": group, "expected": e, "actual": a})
    return diffs


# ----- MIG-P4: bank trailer/control files -------------------------------------

GROUP_SEP = "|"


def _file_group(contract, group_by, parts) -> tuple:
    """Trailer files hold SOURCE-domain group values ('A','CHK01'); translate them
    through the contract's map() transforms into target domain ('ACTIVE','CHECKING')
    before comparing to the computed totals."""
    mappings = contract.raw.get("mappings") or {}
    out = []
    for col_name, part in zip(group_by, parts, strict=True):
        spec = _colspec(contract, col_name)
        value = part
        m = _MAP_RE.match(spec.get("transform") or "")
        if m:
            table = mappings.get(m.group(2)) or {}
            if part not in table:
                raise ValueError(
                    f"control file group value {part!r} for {col_name!r} is not in "
                    f"mapping {m.group(2)!r}"
                )
            value = table[part]
        out.append(None if _isna(value) else normalize(value, spec))
    return tuple(out)


def file_values(control_df, contract, control) -> dict:
    """{group tuple: normalized value} from a name,group,value trailer row set.

    `control_df` is a DataFrame with 'name', 'value', optional 'group' (pipe-
    separated SOURCE-domain group values, empty for ungrouped controls). Values
    are normalized with the contract column spec so '100.1' equals Decimal('100.10').
    """
    fname = control.get("file_name") or control["id"]
    group_by = control.get("group_by") or []
    out: dict = {}
    for row in control_df.itertuples(index=False):
        r = (
            row._asdict()
            if hasattr(row, "_asdict")
            else dict(zip(control_df.columns, row, strict=False))
        )
        if str(r.get("name", "")) != fname:
            continue
        parts = str(r.get("group") or "").split(GROUP_SEP) if group_by else []
        if len(parts) != len(group_by):
            raise ValueError(
                f"control file entry {fname!r} has {len(parts)} group values, "
                f"contract expects {len(group_by)}"
            )
        group = _file_group(contract, group_by, parts)
        if group in out:
            raise ValueError(f"control file has duplicate entry {fname!r} group {group}")
        out[group] = _norm_value(contract, control, str(r["value"]))
    return out


def declared_names(control_df) -> set:
    """All 'name' values present in the trailer file."""
    return {str(v) for v in control_df["name"]} if control_df is not None else set()


def compare_file(file_map: dict, computed: dict, control) -> list:
    """[{control, group, file, actual}] for every group where file != computed."""
    diffs = []
    for group in sorted(file_map.keys() | computed.keys(), key=lambda g: tuple(map(str, g))):
        f, a = file_map.get(group), computed.get(group)
        if f != a:
            diffs.append({"control": control["id"], "group": group, "file": f, "actual": a})
    return diffs
