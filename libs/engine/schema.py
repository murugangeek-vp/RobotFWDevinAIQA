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

# contract type -> Snowflake DDL type (decimal gets per-column scale in
# ddl_for_contract; string gets per-column VARCHAR(n))
SF_TYPE_MAP = {
    "integer": "NUMBER(38,0)",
    "decimal": "NUMBER",
    "string": "VARCHAR",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "boolean": "BOOLEAN",
}

# contract type -> acceptable Snowflake information_schema data_type values.
# NUMBER scale>0 is normalized to DECIMAL by _sf_dtype; the DuckDB-emitted
# names are included so fakesnow runs validate identically.
SF_TYPE_ALIASES = {
    "integer": {"NUMBER", "INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT"},
    "decimal": {"DECIMAL", "NUMERIC", "DEC", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "REAL"},
    "string": {"TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER"},
    "date": {"DATE"},
    "timestamp": {
        "TIMESTAMP",
        "TIMESTAMP_NTZ",
        "TIMESTAMP_LTZ",
        "TIMESTAMP_TZ",
        "DATETIME",
    },
    "boolean": {"BOOLEAN"},
}

# contract type -> MySQL DDL type
MYSQL_TYPE_MAP = {
    "integer": "INT",
    "decimal": "DECIMAL",
    "string": "VARCHAR",
    "date": "DATE",
    "timestamp": "DATETIME",
    "boolean": "TINYINT(1)",
}
MYSQL_TYPE_ALIASES = {
    "integer": {"int", "bigint", "smallint", "mediumint", "tinyint"},
    "decimal": {"decimal", "numeric", "float", "double", "double precision", "real"},
    "string": {"varchar", "char", "text", "tinytext", "mediumtext", "longtext", "enum"},
    "date": {"date"},
    "timestamp": {"datetime", "timestamp"},
    "boolean": {"tinyint", "bit", "boolean", "bool"},
}

# contract type -> Oracle DDL type. Oracle has no integer/boolean storage —
# NUMBER(p,0) / NUMBER(1,0) carry them.
ORACLE_TYPE_MAP = {
    "integer": "NUMBER(19,0)",
    "decimal": "NUMBER",
    "string": "VARCHAR2",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "NUMBER(1,0)",
}
ORACLE_TYPE_ALIASES = {
    # all_tab_columns values are normalized by _ora_dtype (NUMBER scale 0 ->
    # "NUMBER_INT") before alias matching.
    "integer": {"NUMBER_INT", "INTEGER", "INT", "SMALLINT"},
    "decimal": {"NUMBER", "DECIMAL", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"},
    "string": {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "CLOB", "NCLOB", "LONG"},
    "date": {"DATE"},
    "timestamp": {"TIMESTAMP"},
    "boolean": {"NUMBER_INT"},
}

# contract type -> T-SQL (MSSQL) DDL type
MSSQL_TYPE_MAP = {
    "integer": "INT",
    "decimal": "DECIMAL",
    "string": "NVARCHAR",
    "date": "DATE",
    "timestamp": "DATETIME2",
    "boolean": "BIT",
}
MSSQL_TYPE_ALIASES = {
    "integer": {"int", "bigint", "smallint", "tinyint"},
    "decimal": {"decimal", "numeric", "money", "smallmoney", "float", "real"},
    "string": {"nvarchar", "varchar", "nchar", "char", "ntext", "text"},
    "date": {"date"},
    "timestamp": {"datetime", "datetime2", "smalldatetime", "datetimeoffset"},
    "boolean": {"bit"},
}

_TYPE_MAPS = {
    "postgres": PG_TYPE_MAP,
    "snowflake": SF_TYPE_MAP,
    "mysql": MYSQL_TYPE_MAP,
    "oracle": ORACLE_TYPE_MAP,
    "mssql": MSSQL_TYPE_MAP,
}
TYPE_ALIASES = {
    "postgres": PG_TYPE_ALIASES,
    "snowflake": SF_TYPE_ALIASES,
    "mysql": MYSQL_TYPE_ALIASES,
    "oracle": ORACLE_TYPE_ALIASES,
    "mssql": MSSQL_TYPE_ALIASES,
}


def _sf_dtype(data_type, numeric_scale) -> str:
    """Normalize a Snowflake information_schema data_type for alias matching:
    base name uppercased, NUMBER promoted to DECIMAL when numeric_scale > 0."""
    base = (data_type or "").split("(")[0].strip().upper()
    if base == "NUMBER":
        return "DECIMAL" if numeric_scale not in (None, 0) else "NUMBER"
    return base


def _ora_dtype(data_type, data_precision, data_scale) -> str:
    """Normalize an Oracle all_tab_columns data_type for alias matching:
    NUMBER with scale 0 is normalized to NUMBER_INT so contract `integer`
    and `decimal` can be told apart (like _sf_dtype for Snowflake)."""
    base = (data_type or "").split("(")[0].strip().upper()
    if base == "NUMBER":
        return "DECIMAL" if data_scale not in (None, 0) else "NUMBER_INT"
    return base


def ident(name: str, dialect: str = "postgres") -> str:
    """Quote an identifier for the configured SQL dialect. Oracle identifiers
    are emitted quoted-UPPER — the same name an unquoted CREATE produces."""
    if dialect not in TYPE_ALIASES:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")
    if not isinstance(name, str) or not name:
        raise ValueError("SQL identifier must be a nonempty string")
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if dialect == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    if dialect == "oracle":
        name = name.upper()
    return '"' + name.replace('"', '""') + '"'


def contract_from_dict(raw: dict, path: str = "<memory>") -> Contract:
    """Validate a raw contract dict — e.g. one built from an Excel mapping
    workbook — against the contract JSON schema + cross-field semantics."""
    schema = json.loads(_CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))
    js_validate(instance=raw, schema=schema)
    _check_semantics(raw)
    return Contract(raw=raw, path=path)


def load_contract(path: str) -> Contract:
    """Load a YAML contract and validate it against the contract JSON schema."""
    contract_path = Path(path)
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    return contract_from_dict(raw, str(contract_path))


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
    src_cols = set((raw.get("source") or {}).get("columns") or [])
    rf = (raw.get("source") or {}).get("row_filter")
    if rf is not None:
        from libs.engine.reconcile import row_filter_predicates

        for p in row_filter_predicates(rf)[1]:
            if src_cols and p["column"] not in src_cols:
                raise ValueError(f"row_filter column {p['column']!r} not in source.columns")
    for j in (raw.get("source") or {}).get("joins") or []:
        on = j["on"] if isinstance(j["on"], list) else [j["on"]]
        missing = [k for k in on if src_cols and k not in src_cols]
        if missing:
            raise ValueError(f"join {j.get('path')!r}: keys {missing} not in source.columns")
        jcols = j.get("columns")
        if jcols:
            missing_r = [k for k in on if k not in jcols]
            if missing_r:
                raise ValueError(
                    f"join {j.get('path')!r}: keys {missing_r} not in its declared columns"
                )
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
        elif col["type"] == "decimal" and dialect == "snowflake":
            col_type = f"NUMBER(38,{col.get('scale', 2)})"
        null = "" if col["nullable"] else " NOT NULL"
        parts.append(f"{ident(col['name'], dialect)} {col_type}{null}")
    keys = ", ".join(ident(k, dialect) for k in contract.keys)
    parts.append(f"PRIMARY KEY ({keys})")
    cols = ", ".join(parts)
    qualified = f"{ident(contract.schema, dialect)}.{ident(contract.table, dialect)}"
    return f"CREATE TABLE IF NOT EXISTS {qualified} ({cols})"


def _truthy(v) -> bool:
    return str(v).strip().upper() in ("YES", "Y", "TRUE", "1", "PRI")


def _describe_fallback(conn, qualified: str, rows_to_schema: bool):
    """DESCRIBE TABLE fallback for engines whose information_schema is thin
    (e.g. fakesnow). Column layouts differ — Snowflake: name, type, kind,
    null?, ..., primary key; DuckDB: column_name, column_type, null, key —
    so fields are resolved by header name."""
    cur = conn.cursor()
    cur.execute(f"DESCRIBE TABLE {qualified}")
    names = [d[0].lower() for d in cur.description]
    desc = cur.fetchall()
    cur.close()
    name_i = next((i for i, n in enumerate(names) if n in ("name", "column_name")), 0)
    type_i = next((i for i, n in enumerate(names) if n in ("type", "column_type")), 1)
    null_i = next((i for i, n in enumerate(names) if n in ("null?", "null", "is_nullable")), 3)
    key_i = next((i for i, n in enumerate(names) if n in ("primary key", "key")), None)
    schema_rows = [(r[name_i], _sf_dtype(r[type_i], None), _truthy(r[null_i])) for r in desc]
    pk = (
        [r[name_i] for r in desc if key_i is not None and _truthy(r[key_i])]
        if key_i is not None
        else []
    )
    return schema_rows if rows_to_schema else pk


def fetch_db_schema(conn, schema: str, table: str, dialect: str = "postgres") -> list:
    """Live schema from information_schema (or ALL_TAB_COLUMNS for oracle):
    [(name, data_type, nullable), ...]. Snowflake dtypes are normalized via
    _sf_dtype and Oracle via _ora_dtype; the DESCRIBE TABLE fallback covers
    engines with thin information_schema support."""
    if dialect not in TYPE_ALIASES:
        raise ValueError(f"Unsupported SQL dialect: {dialect}")
    cur = conn.cursor()
    if dialect == "postgres":
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        rows = [(r[0], r[1], _truthy(r[2])) for r in cur.fetchall()]
        cur.close()
        return rows
    if dialect in ("mysql", "mssql"):
        ph = "%s" if dialect == "mysql" else "?"
        cur.execute(
            f"""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = {ph} AND table_name = {ph}
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        rows = [(r[0], (r[1] or "").lower(), _truthy(r[2])) for r in cur.fetchall()]
        cur.close()
        return rows
    if dialect == "oracle":
        # ALL_TAB_COLUMNS is grant-filtered; OWNER holds the schema name.
        cur.execute(
            """
            SELECT column_name, data_type, nullable, data_precision, data_scale
            FROM all_tab_columns
            WHERE owner = :1 AND table_name = :2
            ORDER BY column_id
            """,
            (schema.upper(), table.upper()),
        )
        rows = [(r[0], _ora_dtype(r[1], r[3], r[4]), _truthy(r[2])) for r in cur.fetchall()]
        cur.close()
        return rows
    qualified = f"{ident(schema, dialect)}.{ident(table, dialect)}"
    try:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        rows = [(r[0], _sf_dtype(r[1], r[3]), _truthy(r[2])) for r in cur.fetchall()]
        cur.close()
        if rows:
            return rows
    except Exception:  # noqa: BLE001 — fall through to DESCRIBE
        cur.close()
    try:
        return _describe_fallback(conn, qualified, rows_to_schema=True)
    except Exception:  # noqa: BLE001 — table missing => empty schema, not a crash
        raise


def fetch_primary_key(conn, schema: str, table: str, dialect: str = "postgres") -> list:
    cur = conn.cursor()
    if dialect == "mysql":
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.key_column_usage
            WHERE constraint_name = 'PRIMARY'
              AND table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    if dialect == "mssql":
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
             AND tc.table_name = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = ? AND tc.table_name = ?
            ORDER BY kcu.ordinal_position
            """,
            (schema, table),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    if dialect == "oracle":
        cur.execute(
            """
            SELECT cc.column_name
            FROM all_constraints c
            JOIN all_cons_columns cc
              ON c.owner = cc.owner
             AND c.constraint_name = cc.constraint_name
            WHERE c.constraint_type = 'P'
              AND c.owner = :1 AND c.table_name = :2
            ORDER BY cc.position
            """,
            (schema.upper(), table.upper()),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    if dialect == "snowflake":
        qualified = f"{ident(schema, dialect)}.{ident(table, dialect)}"
        try:
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_schema = kcu.constraint_schema
                 AND tc.constraint_name = kcu.constraint_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
                ORDER BY kcu.ordinal_position
                """,
                (schema, table),
            )
            rows = [r[0] for r in cur.fetchall()]
            cur.close()
            if rows:
                return rows
        except Exception:  # noqa: BLE001 — fall through to DESCRIBE
            cur.close()
        try:
            return _describe_fallback(conn, qualified, rows_to_schema=False)
        except Exception:  # noqa: BLE001 — no PK info available
            raise
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
