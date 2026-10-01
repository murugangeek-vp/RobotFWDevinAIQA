import json
from pathlib import Path

import yaml
from jsonschema import validate as js_validate

from libs.engine.models import Contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_SCHEMA_PATH = _REPO_ROOT / "config" / "contracts" / "contract.schema.json"

# contract type -> PostgreSQL DDL type
PG_TYPE_MAP = {
    "integer": "integer",
    "decimal": "numeric",
    "string": "character varying",
    "date": "date",
    "timestamp": "timestamp without time zone",
    "boolean": "boolean",
}

# contract type -> acceptable information_schema data_type values
PG_TYPE_ALIASES = {
    "integer": {"integer", "bigint", "smallint"},
    "decimal": {"numeric"},
    "string": {"character varying", "text"},
    "date": {"date"},
    "timestamp": {"timestamp without time zone", "timestamp with time zone"},
    "boolean": {"boolean"},
}

# MySQL dialect equivalents (information_schema reports lowercase types)
MYSQL_TYPE_MAP = {
    "integer": "INT",
    "decimal": "DECIMAL",
    "string": "VARCHAR",
    "date": "DATE",
    "timestamp": "DATETIME",
    "boolean": "TINYINT(1)",
}

MYSQL_TYPE_ALIASES = {
    "integer": {"int", "bigint", "smallint", "tinyint", "mediumint"},
    "decimal": {"decimal"},
    "string": {"varchar", "char", "text"},
    "date": {"date"},
    "timestamp": {"datetime", "timestamp"},
    "boolean": {"tinyint"},
}

_TYPE_MAPS = {"postgres": PG_TYPE_MAP, "mysql": MYSQL_TYPE_MAP}
TYPE_ALIASES = {"postgres": PG_TYPE_ALIASES, "mysql": MYSQL_TYPE_ALIASES}
_QUOTE = {"postgres": '"', "mysql": "`"}


def load_contract(path: str) -> Contract:
    """Load a YAML contract and validate it against the contract JSON schema."""
    contract_path = Path(path)
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    schema = json.loads(_CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    js_validate(instance=raw, schema=schema)
    return Contract(raw=raw, path=str(contract_path))


def ddl_for_contract(contract: Contract, dialect: str = "postgres") -> str:
    """CREATE TABLE DDL derived from the contract (used by the loader)."""
    type_map = _TYPE_MAPS.get(dialect, PG_TYPE_MAP)
    q = _QUOTE.get(dialect, '"')
    parts = []
    for col in contract.columns:
        col_type = type_map[col["type"]]
        if col["type"] == "string":
            col_type = f"{type_map['string']}({col.get('max_length', 255)})"
        elif col["type"] == "decimal" and dialect == "mysql":
            col_type = f"DECIMAL(18,{col.get('scale', 2)})"
        null = "" if col["nullable"] else " NOT NULL"
        parts.append(f"{q}{col['name']}{q} {col_type}{null}")
    keys = ", ".join(f"{q}{k}{q}" for k in contract.keys)
    parts.append(f"PRIMARY KEY ({keys})")
    cols = ", ".join(parts)
    return f"CREATE TABLE IF NOT EXISTS {q}{contract.schema}{q}.{q}{contract.table}{q} ({cols})"


def fetch_db_schema(conn, schema: str, table: str) -> list:
    """Live schema from information_schema: [(name, data_type, nullable), ...]."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    rows = [(r[0], r[1], r[2] == "YES") for r in cur.fetchall()]
    cur.close()
    return rows


def fetch_primary_key(conn, schema: str, table: str) -> list:
    cur = conn.cursor()
    # pg_catalog is used rather than information_schema.key_column_usage, which
    # hides constraints from non-owner roles (recon_ro).
    cur.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a
          ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE i.indisprimary AND n.nspname = %s AND c.relname = %s
        ORDER BY array_position(i.indkey, a.attnum)
        """,
        (schema, table),
    )
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


SCHEMA_ASPECTS = ("columns", "types", "nullability", "primary_key")


def validate_target_schema_by_category(adapter, contract: Contract) -> dict:
    """Compare live DB schema to the contract, via the target adapter.

    Returns {aspect: [error strings]} for each aspect in SCHEMA_ASPECTS.
    If the table is missing, every aspect reports it — nothing is verifiable.
    """
    errors: dict[str, list] = {a: [] for a in SCHEMA_ASPECTS}
    aliases_map = TYPE_ALIASES.get(getattr(adapter, "dialect", "postgres"), PG_TYPE_ALIASES)
    live = adapter.schema(contract.table)
    if not live:
        msg = f"table {contract.schema}.{contract.table} does not exist"
        for a in SCHEMA_ASPECTS:
            errors[a].append(msg)
        return errors

    live_by_name = {name: (dtype, nullable) for name, dtype, nullable in live}
    expected_names = [c["name"] for c in contract.columns]
    live_names = [name for name, _, _ in live]

    if live_names != expected_names:
        errors["columns"].append(
            f"column order/count mismatch: expected {expected_names}, got {live_names}"
        )

    for col in contract.columns:
        name = col["name"]
        if name not in live_by_name:
            errors["columns"].append(f"missing column '{name}'")
            continue
        dtype, nullable = live_by_name[name]
        allowed = aliases_map[col["type"]]
        if dtype not in allowed:
            errors["types"].append(
                f"column '{name}' type mismatch: expected {col['type']} ({sorted(allowed)}), got {dtype}"
            )
        if nullable != col["nullable"]:
            errors["nullability"].append(
                f"column '{name}' nullability mismatch: expected nullable={col['nullable']}, got {nullable}"
            )

    pk = adapter.primary_key(contract.table)
    if pk != contract.keys:
        errors["primary_key"].append(f"primary key mismatch: expected {contract.keys}, got {pk}")
    return errors


def validate_target_schema(adapter, contract: Contract) -> list:
    """Flat, de-duplicated list of schema errors across all aspects."""
    by_cat = validate_target_schema_by_category(adapter, contract)
    errors = []
    for aspect in SCHEMA_ASPECTS:
        for e in by_cat[aspect]:
            if e not in errors:
                errors.append(e)
    return errors
