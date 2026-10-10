"""Compile a parsed TableMapping + workbook code tables into a validated
engine Contract — the same object load_contract() produces for YAML files,
so every downstream check (DQ rules, transforms, compare, schema, loader)
works unchanged.
"""

import re

from libs.engine.models import Contract
from libs.engine.schema import contract_from_dict
from libs.mapping.models import TableMapping

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def _source_columns(fields: list, code_mappings: dict) -> list:
    """Columns the source SELECT must fetch: declared Source Columns plus
    every column reference found inside transformation expressions —
    {placeholder} tokens and unquoted function args (map/casewhen style).
    Quoted literals and Mapping-sheet names are not columns."""
    cols: list = []

    def add(c):
        if c and c not in cols:
            cols.append(c)

    from libs.engine.mapping_transforms import transform_inputs

    for f in fields:
        add(f.source_column)
        if f.transformation:
            for name in sorted(transform_inputs(f.transformation, code_mappings)):
                add(name)
    return cols


def build_contract(
    tm: TableMapping,
    code_mappings: dict,
    source_cfg: dict,
    target_cfg: dict,
    workbook_path: str = "",
) -> Contract:
    """Build the validated Contract for one TableMapping.

    source_cfg: env `source:` block (host/port/database/…, ${VAR} already
    expanded). target_cfg: env `target:` block (for type + schema default).
    """
    fields = [f for f in tm.fields if f.enabled]
    if len(set(tm.keys)) != len(tm.keys):
        raise ValueError("Duplicate reconciliation key columns")
    source = {
        **source_cfg,
        "schema": tm.source_schema,
        "table": tm.source_table,
        "columns": _source_columns(fields, code_mappings),
    }
    for name in [
        tm.source_schema,
        tm.source_table,
        tm.target_schema,
        tm.target_table,
        *source["columns"],
        *(f.target_column for f in fields),
    ]:
        if not _IDENT_RE.fullmatch(name):
            raise ValueError(f"Invalid mapping identifier: {name!r}")
    if tm.where:
        source["where"] = tm.where
    if tm.batch_size:
        source["batch_size"] = tm.batch_size

    columns = []
    for f in fields:
        col = {
            "name": f.target_column,
            "type": f.target_type,
            "nullable": f.nullable,
            "exact": True,
        }
        if f.transformation:
            col["transform"] = f.transformation
        else:
            col["source_name"] = f.source_column
        if f.max_length:
            col["max_length"] = f.max_length
        if f.scale is not None:
            col["scale"] = f.scale
        if f.target_column in tm.keys:
            if f.nullable:
                raise ValueError("Reconciliation keys must be non-nullable")
            col["unique"] = len(tm.keys) == 1
        columns.append(col)

    raw: dict = {
        "contract_name": tm.mapping_id,
        "version": "1.0",
        "description": (
            f"Excel-driven mapping {tm.source_schema}.{tm.source_table} -> "
            f"{tm.target_schema}.{tm.target_table}"
        ),
        "source": source,
        "target": {
            "type": target_cfg.get("type", "snowflake"),
            "schema": tm.target_schema or target_cfg.get("schema"),
            "table": tm.target_table,
        },
        "keys": tm.keys,
        "columns": columns,
    }
    if code_mappings:
        raw["mappings"] = code_mappings
    if tm.recon_mode != "full" or tm.batch_size:
        raw["recon"] = {"mode": tm.recon_mode}
        if tm.batch_size:
            raw["recon"]["batch_size"] = tm.batch_size
    return contract_from_dict(raw, path=workbook_path)
