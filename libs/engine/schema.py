import json
from pathlib import Path

import yaml
from jsonschema import validate as js_validate

from libs.engine.models import Contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_SCHEMA_PATH = _REPO_ROOT / "config" / "contracts" / "contract.schema.json"

# contract type -> PostgreSQL type
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


def load_contract(path: str) -> Contract:
    """Load a YAML contract and validate it against the contract JSON schema."""
    contract_path = Path(path)
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    schema = json.loads(_CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    js_validate(instance=raw, schema=schema)
    return Contract(raw=raw, path=str(contract_path))


def ddl_for_contract(contract: Contract) -> str:
    """CREATE TABLE DDL derived from the contract (used by the loader)."""
    parts = []
    for col in contract.columns:
        pg_type = PG_TYPE_MAP[col["type"]]
        if col["type"] == "string" and col.get("max_length"):
            pg_type = f"character varying({col['max_length']})"
        null = "" if col["nullable"] else " NOT NULL"
        parts.append(f'"{col["name"]}" {pg_type}{null}')
    keys = ", ".join(f'"{k}"' for k in contract.keys)
    parts.append(f"PRIMARY KEY ({keys})")
    cols = ", ".join(parts)
    return f"CREATE TABLE IF NOT EXISTS {contract.schema}.{contract.table} ({cols})"


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


def validate_target_schema_by_category(conn, contract: Contract) -> dict:
    """Compare live DB schema to the contract.

    Returns {aspect: [error strings]} for each aspect in SCHEMA_ASPECTS.
    If the table is missing, every aspect reports it — nothing is verifiable.
    """
    errors = {a: [] for a in SCHEMA_ASPECTS}
    live = fetch_db_schema(conn, contract.schema, contract.table)
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
        allowed = PG_TYPE_ALIASES[col["type"]]
        if dtype not in allowed:
            errors["types"].append(
                f"column '{name}' type mismatch: expected {col['type']} ({sorted(allowed)}), got {dtype}"
            )
        if nullable != col["nullable"]:
            errors["nullability"].append(
                f"column '{name}' nullability mismatch: expected nullable={col['nullable']}, got {nullable}"
            )

    pk = fetch_primary_key(conn, contract.schema, contract.table)
    if pk != contract.keys:
        errors["primary_key"].append(
            f"primary key mismatch: expected {contract.keys}, got {pk}"
        )
    return errors


def validate_target_schema(conn, contract: Contract) -> list:
    """Flat, de-duplicated list of schema errors across all aspects."""
    by_cat = validate_target_schema_by_category(conn, contract)
    errors = []
    for aspect in SCHEMA_ASPECTS:
        for e in by_cat[aspect]:
            if e not in errors:
                errors.append(e)
    return errors
