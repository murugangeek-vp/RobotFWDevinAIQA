"""MIG-06: migration manifest — the single list of tables in scope.

Validation is fail-closed: schema, unique names, contract paths confined to the
repository, contracts load and are named after their table, unique target tables,
known and acyclic dependencies, and relationships between enabled tables whose
columns exist with matching types.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import validate as js_validate

from libs.engine.models import Contract
from libs.engine.schema import load_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = REPO_ROOT / "config" / "migration" / "manifest.schema.json"


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class TableSpec:
    name: str
    contract: Contract
    contract_path: Path
    tier: str = "standard"
    enabled: bool = True
    depends_on: list = field(default_factory=list)
    full_compare: bool = True


@dataclass
class Manifest:
    raw: dict
    path: Path
    tables: dict  # name -> TableSpec, manifest order
    relationships: list

    @property
    def name(self) -> str:
        return self.raw["migration"]

    @property
    def version(self) -> str:
        return self.raw["version"]

    @property
    def classification(self) -> str:
        return self.raw["data_classification"]

    def enabled_tables(self) -> list:
        """Enabled table names in dependency order (parents before children)."""
        return [n for n in self.order() if self.tables[n].enabled]

    def order(self) -> list:
        done: list = []
        pending = list(self.tables)
        while pending:
            ready = [n for n in pending if all(d in done for d in self.tables[n].depends_on)]
            if not ready:
                raise ValueError(f"manifest dependency cycle among {sorted(pending)}")
            done.extend(ready)
            pending = [n for n in pending if n not in ready]
        return done

    def file_hashes(self) -> dict:
        """SHA-256 of the manifest and every contract — proves which rules ran."""
        hashes = {str(self.path.relative_to(REPO_ROOT)).replace("\\", "/"): sha256_file(self.path)}
        for spec in self.tables.values():
            rel = str(spec.contract_path.relative_to(REPO_ROOT)).replace("\\", "/")
            hashes[rel] = sha256_file(spec.contract_path)
        return hashes


def _resolve_contract(path: str) -> Path:
    resolved = (REPO_ROOT / path).resolve()
    if not resolved.is_relative_to(REPO_ROOT):
        raise ValueError(f"contract path escapes the repository: {path!r}")
    if not resolved.is_file():
        raise ValueError(f"contract not found: {path!r}")
    return resolved


def load_manifest(path) -> Manifest:
    manifest_path = Path(path).resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    js_validate(instance=raw, schema=json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))

    tables: dict = {}
    target_tables: dict = {}
    for entry in raw["tables"]:
        name = entry["name"]
        if name in tables:
            raise ValueError(f"duplicate manifest table {name!r}")
        contract_path = _resolve_contract(entry["contract"])
        contract = load_contract(str(contract_path))
        if contract.name != name:
            raise ValueError(f"table {name!r}: contract_name is {contract.name!r}")
        target = contract.table.lower()
        if target in target_tables:
            raise ValueError(
                f"tables {target_tables[target]!r} and {name!r} share target {target!r}"
            )
        target_tables[target] = name
        tables[name] = TableSpec(
            name=name,
            contract=contract,
            contract_path=contract_path,
            tier=entry.get("tier", "standard"),
            enabled=entry.get("enabled", True),
            depends_on=list(entry.get("depends_on", [])),
            full_compare=entry.get("full_compare", True),
        )
    for spec in tables.values():
        unknown = [d for d in spec.depends_on if d not in tables]
        if unknown:
            raise ValueError(f"table {spec.name!r} depends on unknown tables {unknown}")

    rel_ids = set()
    for rel in raw.get("relationships", []):
        if rel["id"] in rel_ids:
            raise ValueError(f"duplicate relationship id {rel['id']!r}")
        rel_ids.add(rel["id"])
        ends = {}
        for side, col_key in (("child", "column"), ("parent", "parent_column")):
            table = tables.get(rel[side])
            if table is None:
                raise ValueError(f"relationship {rel['id']!r}: unknown {side} {rel[side]!r}")
            if not table.enabled:
                raise ValueError(f"relationship {rel['id']!r}: {side} {rel[side]!r} is disabled")
            try:
                ends[side] = table.contract.column(rel[col_key])
            except KeyError:
                raise ValueError(
                    f"relationship {rel['id']!r}: {rel[side]!r} has no column {rel[col_key]!r}"
                ) from None
        if ends["child"]["type"] != ends["parent"]["type"]:
            raise ValueError(f"relationship {rel['id']!r}: column types differ")

    manifest = Manifest(
        raw=raw, path=manifest_path, tables=tables, relationships=raw.get("relationships", [])
    )
    manifest.order()  # raises on cycles
    return manifest
