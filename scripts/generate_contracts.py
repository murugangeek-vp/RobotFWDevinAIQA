"""MIG-P1: draft migration contracts from the bank's mapping spec.

    python scripts/generate_contracts.py \
        --spec config/migration/mapping_spec.csv \
        --headers-dir data/samples/bank \
        --mappings config/migration/code_mappings.csv \
        --out-dir config/contracts/migration

Spec CSV (one row per target column):
    contract,target_table,column,source_name,type,nullable,key,pii,transform,scale,max_length,unique

    contract      contract/table name, e.g. account
    target_table  target table name, e.g. bank_account
    column        target column name
    source_name   source column name (transforms may reference these)
    type          integer|decimal|string|date|timestamp|boolean
    nullable      true|false
    key           true on reconciliation key columns
    pii           true if regulated/personal data — reviewer MUST confirm
    transform     optional expression, e.g. "lower({email_addr})" or
                  "map(status_cd, account_status)" — MUST be CSV-quoted
                  because it contains a comma
    scale         decimal scale (decimal columns only)
    max_length    optional string bound
    unique        true|false

Optional --mappings CSV: mapping_name,source_value,target_value
Optional --headers-dir: <dir>/<contract>/*.csv — actual extract headers; every
spec source_name must appear there, and source.columns comes from the real file.

Output contracts are marked DRAFT — a reviewer still confirms pii flags,
controls (control totals), and mappings before they are committed.
"""

import argparse
import csv
import re
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

BOOL = {"true": True, "false": False, "yes": True, "no": False, "": False}
TYPES = {"integer", "decimal", "string", "date", "timestamp", "boolean"}


def _bool(v: str, field: str) -> bool:
    key = str(v).strip().lower()
    if key not in BOOL:
        raise ValueError(f"{field}: expected true/false, got {v!r}")
    return BOOL[key]


def load_spec(path: Path) -> "OrderedDict[str, dict]":
    contracts: OrderedDict[str, dict] = OrderedDict()
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    required = {"contract", "target_table", "column", "type"}
    for i, row in enumerate(rows, start=2):
        missing = required - {k for k, v in row.items() if str(v or "").strip()}
        if missing:
            raise ValueError(f"{path}:{i} missing {sorted(missing)}")
        name = row["contract"].strip()
        c = contracts.setdefault(
            name,
            {"target_table": row["target_table"].strip(), "keys": [], "columns": []},
        )
        if c["target_table"] != row["target_table"].strip():
            raise ValueError(f"{path}:{i} target_table differs within contract {name!r}")
        col = {"name": row["column"].strip(), "type": row["type"].strip()}
        if col["type"] not in TYPES:
            raise ValueError(f"{path}:{i} unknown type {col['type']!r}")
        if row.get("source_name", "").strip():
            col["source_name"] = row["source_name"].strip()
        col["nullable"] = _bool(row.get("nullable", "true"), "nullable")
        for flag in ("pii", "unique"):
            if _bool(row.get(flag, ""), flag):
                col[flag] = True
        if row.get("scale", "").strip():
            col["scale"] = int(row["scale"])
        if row.get("max_length", "").strip():
            col["max_length"] = int(row["max_length"])
        if row.get("transform", "").strip():
            col["transform"] = row["transform"].strip()
        c["columns"].append(col)
        if _bool(row.get("key", ""), "key"):
            c["keys"].append(col["name"])
    if not contracts:
        raise ValueError(f"{path} contains no rows")
    return contracts


def load_mappings(path: "Path | None") -> dict:
    out: dict = {}
    if not path:
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            name, src, tgt = (
                row["mapping"].strip(),
                row["source_value"].strip(),
                row["target_value"].strip(),
            )
            if not (name and src):
                raise ValueError(f"{path}:{i} mapping/source_value required")
            out.setdefault(name, {})[src] = tgt
    return out


def header_for(headers_dir: Path, contract: str) -> list:
    parts = sorted((headers_dir / contract).glob("*.csv"))
    if not parts:
        raise ValueError(f"no csv part under {headers_dir / contract}")
    with open(parts[0], newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def referenced_source_columns(col: dict) -> list:
    expr = col.get("transform", "")
    refs = re.findall(r"\{(\w+)\}", expr)
    refs += re.findall(r"map\(\s*(\w+)\s*,", expr)
    refs += re.findall(r"(?:lower|upper|strip)\(\s*\{?(\w+)\}?\s*\)", expr)
    if not refs and col.get("source_name"):
        refs.append(col["source_name"])
    return refs


def build_contract(name: str, spec: dict, header: "list | None", mappings: dict) -> dict:
    source_cols = header or []
    refs = []
    for col in spec["columns"]:
        refs += referenced_source_columns(col)
    if header:
        unknown = [r for r in refs if r not in header]
        if unknown:
            raise ValueError(
                f"{name}: spec references source columns absent from the extract header: {unknown}"
            )
    else:
        source_cols = list(dict.fromkeys(refs))
    if not spec["keys"]:
        raise ValueError(f"{name}: spec marks no key columns — set key=true")
    used_mappings = {}
    for col in spec["columns"]:
        expr = col.get("transform", "")
        for m in re.findall(r"map\(\s*\w+\s*,\s*(\w+)\s*\)", expr):
            if m not in mappings:
                raise ValueError(f"{name}: transform uses undefined mapping {m!r}")
            used_mappings[m] = mappings[m]
    return {
        "contract_name": name,
        "version": "0.1.0-draft",
        "description": (
            "DRAFT generated from the mapping spec — REVIEW REQUIRED before use: "
            "confirm pii flags, controls, and code mappings against the approved spec."
        ),
        "source": {
            "type": "s3",
            "prefix": f"webster/{name}/",
            "suffix": ".csv",
            "format": "csv",
            "encoding": "utf-8",
            "delimiter": ",",
            "columns": source_cols,
        },
        "target": {"type": "snowflake", "table": spec["target_table"]},
        "keys": spec["keys"],
        "mappings": used_mappings or None,
        "controls": [],
        "columns": spec["columns"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--mappings", type=Path)
    ap.add_argument("--headers-dir", type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    specs = load_spec(args.spec)
    mappings = load_mappings(args.mappings)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in specs.items():
        header = header_for(args.headers_dir, name) if args.headers_dir else None
        contract = build_contract(name, spec, header, mappings)
        if contract["mappings"] is None:
            contract.pop("mappings")
        out = args.out_dir / f"{name}.yaml"
        out.write_text(
            yaml.safe_dump(contract, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"wrote {out} ({len(contract['columns'])} columns, keys={contract['keys']})")
    print("\nDRAFT ONLY — review pii flags and add control totals before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
