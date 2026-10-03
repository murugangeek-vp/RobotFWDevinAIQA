import json
import re
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

# Snowflake dialect equivalents (adapter normalizes live names/types to
# lowercase — unquoted identifiers fold to UPPERCASE server-side)
SNOWFLAKE_TYPE_MAP = {
    "integer": "NUMBER(38,0)",
    "decimal": "NUMBER",
    "string": "VARCHAR",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "boolean": "BOOLEAN",
}

SNOWFLAKE_TYPE_ALIASES = {
    "integer": {"number"},
    "decimal": {"number", "decimal", "numeric"},
    "string": {"varchar", "text", "string", "char"},
    "date": {"date"},
    "timestamp": {"timestamp_ntz"},
    "boolean": {"boolean"},
}

_TYPE_MAPS = {"postgres": PG_TYPE_MAP, "mysql": MYSQL_TYPE_MAP, "snowflake": SNOWFLAKE_TYPE_MAP}
TYPE_ALIASES = {
    "postgres": PG_TYPE_ALIASES,
    "mysql": MYSQL_TYPE_ALIASES,
    "snowflake": SNOWFLAKE_TYPE_ALIASES,
}


def ident(name: str, dialect: str = "postgres") -> str:
    """Quote an identifier; Snowflake supports conventional folded identifiers only."""
    if dialect not in TYPE_ALIASES:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")
    if not isinstance(name, str) or not name:
        raise ValueError("SQL identifier must be a nonempty string")
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if dialect == "snowflake":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,254}", name):
            raise ValueError("Snowflake identifiers must use unquoted identifier syntax")
        name = name.upper()
    return '"' + name.replace('"', '""') + '"'


def load_contract(path: str) -> Contract:
    """Load a YAML contract and validate it against the contract JSON schema."""
    contract_path = Path(path)
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    schema = json.loads(_CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    js_validate(instance=raw, schema=schema)
    _check_semantics(raw)
    return Contract(raw=raw, path=str(contract_path))


_NUMERIC = ("integer", "decimal")


def _check_semantics(raw: dict) -> None:
    """Cross-field rules JSON schema cannot express; fail closed on ambiguity."""
    names = [c["name"] for c in raw["columns"]]
    if len(names) != len(set(names)):
        raise ValueError("contract has duplicate column names")
    by_name = {c["name"]: c for c in raw["columns"]}
    missing_keys = [k for k in raw["keys"] if k not in by_name]
    if missing_keys:
        raise ValueError(f"contract keys are not columns: {missing_keys}")
    for name, table in (raw.get("mappings") or {}).items():
        # YAML turns unquoted 00/Y/1 into ints/bools, silently losing codes.
        if not all(isinstance(k, str) for k in table):
            raise ValueError(f"mapping {name!r} has non-string codes; quote every code in YAML")
    seen = set()
    for ctl in raw.get("controls") or []:
        if ctl["id"] in seen:
            raise ValueError(f"duplicate control id {ctl['id']!r}")
        seen.add(ctl["id"])
        col = ctl.get("column")
        if ctl["type"] == "count":
            if col is not None:
                raise ValueError(f"control {ctl['id']!r}: count takes no column")
        elif col not in by_name:
            raise ValueError(f"control {ctl['id']!r}: unknown column {col!r}")
        if ctl["type"] == "sum" and by_name[col]["type"] not in _NUMERIC:
            raise ValueError(f"control {ctl['id']!r}: sum requires a numeric column")
        for g in ctl.get("group_by") or []:
            if g not in by_name:
                raise ValueError(f"control {ctl['id']!r}: unknown group_by column {g!r}")


def ddl_for_contract(contract: Contract, dialect: str = "postgres") -> str:
    """CREATE TABLE DDL derived from the contract (used by the loader)."""
    if dialect not in _TYPE_MAPS:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")
    type_map = _TYPE_MAPS[dialect]
    parts = []
    for col in contract.columns:
        col_type = type_map[col["type"]]
        if col["type"] == "string":
            col_type = f"{type_map['string']}({col.get('max_length', 255)})"
        elif col["type"] == "decimal" and dialect in ("mysql", "snowflake"):
            width = "DECIMAL(18,{})" if dialect == "mysql" else "NUMBER(18,{})"
            col_type = width.format(col.get("scale", 2))
        null = "" if col["nullable"] else " NOT NULL"
        parts.append(f"{ident(col['name'], dialect)} {col_type}{null}")
    keys = ", ".join(ident(k, dialect) for k in contract.keys)
    parts.append(f"PRIMARY KEY ({keys})")
    cols = ", ".join(parts)
    qualified = f"{ident(contract.schema, dialect)}.{ident(contract.table, dialect)}"
    return f"CREATE TABLE IF NOT EXISTS {qualified} ({cols})"


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
    dialect = adapter.dialect
    if dialect not in TYPE_ALIASES:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")
    aliases_map = TYPE_ALIASES[dialect]
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

    if dialect == "snowflake":
        errors["types"].extend(adapter.type_errors(contract))
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
