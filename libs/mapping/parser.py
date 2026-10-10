"""Parse the migration mapping workbook (bank_mapping.xlsx).

Sheets:
  Tables   — one row per source->target table pair:
             Mapping ID, Source Schema, Source Table, Target Schema,
             Target Table, Key Columns, Where Clause, Recon Mode,
             Batch Size, Enabled
  FieldMap — one row per field: Mapping ID, Source Column, Target Column,
             Target Type, Nullable, Max Length, Scale, Transformation, Enabled
  Mapping  — code translation tables: Mapping Name, Code, Value
  README   — free-text documentation (ignored by the parser)

Every shape problem raises immediately (fail closed) — a broken workbook row
must never silently pass a migration test.
"""

from pathlib import Path

import openpyxl

from libs.excelrules.validators import where_is_safe
from libs.mapping.models import CONTRACT_TYPES, RECON_MODES, FieldMapping, TableMapping

TABLES_SHEET = "Tables"
FIELDMAP_SHEET = "FieldMap"
MAPPING_SHEET = "Mapping"

_TABLES_REQUIRED = [
    "mapping id",
    "source schema",
    "source table",
    "target schema",
    "target table",
    "key columns",
    "enabled",
]
_FIELDMAP_REQUIRED = [
    "mapping id",
    "source column",
    "target column",
    "target type",
    "nullable",
    "enabled",
]


def _truth(v) -> bool:
    text = str(v).strip().lower()
    if text not in ("yes", "y", "true", "1", "no", "n", "false", "0"):
        raise ValueError("Boolean cells must explicitly contain yes/no or true/false")
    return text in ("yes", "y", "true", "1")


def _rows_by_header(ws):
    """Yield {normalized_header: cell_value} dicts for each populated row."""
    rows = ws.iter_rows(values_only=True)
    headers = [str(h or "").strip().lower() for h in next(rows, ())]
    required = {
        TABLES_SHEET: _TABLES_REQUIRED,
        FIELDMAP_SHEET: _FIELDMAP_REQUIRED,
        MAPPING_SHEET: ["mapping name", "code", "value"],
    }[ws.title]
    if len(headers) != len(set(headers)) or set(required) - set(headers):
        raise ValueError(f"{ws.title}: missing or duplicate headers")
    for row in rows:
        if any(isinstance(v, str) and v.startswith("=") for v in row):
            raise ValueError(f"{ws.title}: Excel formulas are not allowed")
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        yield {h: v for h, v in zip(headers, row, strict=False) if h}


def _need(row: dict, col: str, ctx: str):
    v = row.get(col)
    if v is None or str(v).strip() == "":
        raise ValueError(f"{ctx}: missing required value for '{col}'")
    return str(v).strip()


def _opt(row: dict, col: str):
    v = row.get(col)
    return None if v is None or str(v).strip() == "" else str(v).strip()


def _opt_int(row: dict, col: str, ctx: str):
    v = _opt(row, col)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"{ctx}: '{col}' must be an integer, got {v!r}") from None


def _read_rows(path, sheet, optional=False):
    from contextlib import closing

    with closing(openpyxl.load_workbook(path, read_only=False, data_only=False)) as wb:
        if sheet not in wb.sheetnames:
            if optional:
                return []
            raise ValueError(f"Missing {sheet} sheet")
        return list(_rows_by_header(wb[sheet]))


def load_tables(path: str) -> "list[TableMapping]":
    tables: list[TableMapping] = []
    seen = set()
    for row in _read_rows(path, TABLES_SHEET):
        ctx = f"Tables row {len(tables) + 2}"
        mid = _need(row, "mapping id", ctx)
        if mid in seen:
            raise ValueError(f"{ctx}: duplicate Mapping ID {mid!r}")
        seen.add(mid)
        keys = [k.strip() for k in _need(row, "key columns", ctx).split(",") if k.strip()]
        if not keys:
            raise ValueError(f"{ctx}: Key Columns must name at least one column")
        where = _opt(row, "where clause")
        if where and not where_is_safe(where):
            raise ValueError(f"{ctx}: unsafe WHERE fragment {where!r}")
        mode = (_opt(row, "recon mode") or "full").lower()
        if mode not in RECON_MODES:
            raise ValueError(f"{ctx}: Recon Mode must be one of {sorted(RECON_MODES)}")
        tables.append(
            TableMapping(
                mapping_id=mid,
                source_schema=_need(row, "source schema", ctx),
                source_table=_need(row, "source table", ctx),
                target_schema=_need(row, "target schema", ctx),
                target_table=_need(row, "target table", ctx),
                keys=keys,
                where=where,
                enabled=_truth(row.get("enabled")),
                recon_mode=mode,
                batch_size=_opt_int(row, "batch size", ctx),
            )
        )
    if not tables:
        raise ValueError(f"{path}: '{TABLES_SHEET}' sheet has no mapping rows")
    return tables


def load_fields(path: str, tables: "list[TableMapping]") -> None:
    """Attach FieldMap rows to their TableMapping (in place)."""
    by_id = {t.mapping_id: t for t in tables}
    seen_targets: dict[str, set] = {t.mapping_id: set() for t in tables}
    for n, row in enumerate(_read_rows(path, FIELDMAP_SHEET), 1):
        ctx = f"FieldMap row {n + 1}"
        mid = _need(row, "mapping id", ctx)
        if mid not in by_id:
            raise ValueError(f"{ctx}: Mapping ID {mid!r} not declared on Tables sheet")
        ttype = _need(row, "target type", ctx).lower()
        if ttype not in CONTRACT_TYPES:
            raise ValueError(
                f"{ctx}: unknown Target Type {ttype!r}; expected one of {sorted(CONTRACT_TYPES)}"
            )
        tgt = _need(row, "target column", ctx)
        if tgt in seen_targets[mid]:
            raise ValueError(f"{ctx}: duplicate target column {tgt!r} for {mid!r}")
        seen_targets[mid].add(tgt)
        by_id[mid].fields.append(
            FieldMapping(
                mapping_id=mid,
                source_column=_need(row, "source column", ctx),
                target_column=tgt,
                target_type=ttype,
                nullable=_truth(row.get("nullable")),
                max_length=_opt_int(row, "max length", ctx),
                scale=_opt_int(row, "scale", ctx),
                transformation=_opt(row, "transformation"),
                enabled=_truth(row.get("enabled")),
            )
        )
    for t in tables:
        if not t.enabled:
            continue
        enabled_targets = {f.target_column for f in t.fields if f.enabled}
        missing = [k for k in t.keys if k not in enabled_targets]
        if missing:
            raise ValueError(f"{t.mapping_id}: Key Columns {missing} have no enabled FieldMap row")


def load_code_mappings(path: str) -> dict:
    """Mapping sheet -> {mapping_name: {code: value}} for map() transforms."""
    out: dict[str, dict] = {}
    for n, row in enumerate(_read_rows(path, MAPPING_SHEET, optional=True), 1):
        ctx = f"Mapping row {n + 1}"
        name = _need(row, "mapping name", ctx)
        code = _need(row, "code", ctx)
        value = _need(row, "value", ctx)
        table = out.setdefault(name, {})
        if code in table:
            raise ValueError(f"{ctx}: duplicate mapping code {name}/{code}")
        table[code] = value
    return out


def load_workbook(path: str) -> "tuple[list[TableMapping], dict]":
    """Parse Tables + FieldMap + Mapping. Returns (table_mappings, code_maps)."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"mapping workbook not found: {path}")
    tables = load_tables(path)
    load_fields(path, tables)
    return tables, load_code_mappings(path)
