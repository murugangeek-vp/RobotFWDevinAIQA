"""Aggregate financial controls and bank trailer certification.

`contract.controls` declares totals a reviewer actually signs off —
row counts, balance sums, distinct customers, min/max, null counts —
optionally broken down by up to three group columns. `source.control_file`
points at the bank trailer file certifying those totals (CSV:
name,group,value). Both were schema-declared but previously never
evaluated — a declared control that does not run is a silent PASS, so
evaluation here fails closed on every bad reference.

Aggregates compare EXPECTED (post-transform, contract column names)
against the target, so transform-aware totals (rounded balances, mapped
code groups) line up with what the target should physically contain.
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from libs.engine.models import Contract
from libs.engine.rules import _is_empty

CONTROL_TYPES = ("count", "sum", "min", "max", "null_count", "count_distinct")


def validate_controls(contract: Contract) -> list:
    """Return the declared control specs, failing closed on any bad reference."""
    specs = contract.raw.get("controls") or []
    names = {c["name"] for c in contract.columns}
    seen = set()
    for spec in specs:
        cid, ctype = spec["id"], spec["type"]
        if cid in seen:
            raise ValueError(f"duplicate control id {cid!r}")
        seen.add(cid)
        if ctype not in CONTROL_TYPES:
            raise ValueError(f"control {cid}: unknown type {ctype!r}")
        col = spec.get("column")
        if ctype == "count" and col:
            raise ValueError(f"control {cid}: count does not take a column")
        if ctype != "count" and not col:
            raise ValueError(f"control {cid}: {ctype} requires a column")
        if col and col not in names:
            raise ValueError(f"control {cid}: unknown column {col!r}")
        for group in spec.get("group_by") or []:
            if group not in names:
                raise ValueError(f"control {cid}: unknown group_by column {group!r}")
    return specs


def _dec(v):
    if v is None:
        return None
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _gcanon(v) -> str:
    """Canonical group-key component — cross-DB type drift must not split a
    group: numeric values compare numerically (int 101 vs float 101.0 vs
    '101'), but STRINGS stay literal — '001' and '1' are different
    identifiers in banking."""
    if _is_empty(v):
        return ""
    if isinstance(v, str):
        return v.strip()
    d = _dec(v)
    if d is not None and d == d.to_integral_value():
        return str(int(d))
    return str(v).strip()


def _value(spec_type: str, numeric: bool, v):
    """Canonical compared value for a control cell."""
    if _is_empty(v):
        return None
    return _dec(v) if numeric else str(v).strip()


def compute_controls(chunks, contract: Contract) -> dict:
    """Stream frames (expected OR target) into {control_id: {group: value}}.

    Group key is a tuple of canonical group values, or () ungrouped.
    count_distinct keeps exact sets — banking controls do not approximate.
    """
    specs = validate_controls(contract)
    states: dict = {}
    if not specs:
        return states
    numeric = {
        spec["id"]: (
            bool(spec.get("column"))
            and contract.column(spec["column"])["type"] in ("integer", "decimal")
        )
        for spec in specs
    }
    for chunk in chunks:
        names = {c for spec in specs for c in [spec.get("column")]} | {
            g for spec in specs for g in (spec.get("group_by") or [])
        }
        names.discard(None)
        missing = names - set(chunk.columns)
        if missing:
            raise ValueError(f"control columns absent from compared frame: {sorted(missing)}")
        idx = {name: i for i, name in enumerate(chunk.columns)}
        plan = [
            (
                spec["id"],
                spec["type"],
                idx.get(spec.get("column")),
                tuple(idx[g] for g in (spec.get("group_by") or [])),
            )
            for spec in specs
        ]
        for row in chunk.itertuples(index=False, name=None):
            for cid, ctype, ci, gis in plan:
                key = tuple(_gcanon(row[i]) for i in gis)
                cell = states.setdefault((cid, key), [] if ctype == "count_distinct" else None)
                val = row[ci] if ci is not None else None
                if ctype == "count":
                    states[(cid, key)] = (cell or 0) + 1
                elif ctype == "null_count":
                    states[(cid, key)] = (cell or 0) + (1 if _is_empty(val) else 0)
                elif ctype == "count_distinct":
                    if not _is_empty(val):
                        assert isinstance(cell, list)
                        cell.append(_gcanon(val))
                elif ctype == "sum":
                    d = _dec(val)
                    if d is not None:
                        states[(cid, key)] = (cell or Decimal(0)) + d
                elif ctype == "min":
                    v = _value(ctype, numeric[cid], val)
                    if v is not None and (cell is None or v < cell):
                        states[(cid, key)] = v
                elif ctype == "max":
                    v = _value(ctype, numeric[cid], val)
                    if v is not None and (cell is None or v > cell):
                        states[(cid, key)] = v
    out: dict = {}
    for (cid, key), cell in states.items():
        out.setdefault(cid, {})[key] = len(set(cell)) if isinstance(cell, list) else cell
    return out


def _gstr(key) -> str:
    return " | ".join(key) if key else "(all rows)"


def compare_controls(expected: dict, actual: dict) -> list:
    """Expected (computed source-side) vs actual (target) aggregates."""
    diffs = []
    for cid in expected.keys() | actual.keys():
        eg, ag = expected.get(cid, {}), actual.get(cid, {})
        for group in eg.keys() | ag.keys():
            e, a = eg.get(group), ag.get(group)
            if group not in eg or group not in ag or e != a:
                diffs.append(
                    {
                        "control": cid,
                        "group": _gstr(group),
                        "expected": str(e) if group in eg else "(group absent)",
                        "actual": str(a) if group in ag else "(group absent)",
                    }
                )
    return diffs


def load_control_file(path) -> dict:
    """Parse a bank trailer file -> {file_name: {group_str: value_str}}."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"control file not found: {path}")
    declared: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not {"name", "group", "value"} <= set(reader.fieldnames or []):
            raise ValueError(f"{path}: control file requires name,group,value columns")
        for n, row in enumerate(reader, 2):
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError(f"{path} row {n}: missing control name")
            group, value = (row.get("group") or "").strip(), (row.get("value") or "").strip()
            if group in declared.setdefault(name, {}):
                raise ValueError(f"{path} row {n}: duplicate {name}/{group}")
            declared[name][group] = value
    return declared


def validate_control_file(agg: dict, declared: dict, contract: Contract) -> list:
    """Certify computed controls against the bank trailer — fail closed:
    every declared control needs a certified total, and a certified group
    with no computed counterpart is a mismatch."""
    diffs = []
    for spec in validate_controls(contract):
        cid = spec["id"]
        fname = spec.get("file_name") or cid
        certified = declared.get(fname)
        if certified is None:
            diffs.append(
                {
                    "control": cid,
                    "group": "(all rows)",
                    "expected": "trailer total",
                    "actual": f"{fname!r} absent from control file",
                }
            )
            continue
        computed = agg.get(cid, {})
        seen = set()
        for gstr, dval in certified.items():
            key = tuple(gstr.split(" | ")) if gstr else ()
            seen.add(key)
            cval = computed.get(key)
            dec = _dec(dval)
            if cval is None:
                diffs.append(
                    {
                        "control": cid,
                        "group": gstr or "(all rows)",
                        "expected": dval,
                        "actual": "(no computed rows)",
                    }
                )
            elif (
                dec is not None
                and _dec(cval) is not None
                and _dec(cval) != dec
                or dec is None
                and str(cval) != dval
            ):
                diffs.append(
                    {
                        "control": cid,
                        "group": gstr or "(all rows)",
                        "expected": dval,
                        "actual": str(cval),
                    }
                )
        for key in computed.keys() - seen:
            diffs.append(
                {
                    "control": cid,
                    "group": _gstr(key),
                    "expected": "(not certified)",
                    "actual": str(computed[key]),
                }
            )
    return diffs
