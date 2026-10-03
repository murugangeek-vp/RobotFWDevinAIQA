"""Pilot-08: CSV -> PostgreSQL loader.

Loads the *expected* target rows (contract transforms applied) into the target
table. Runs as recon_rw; refuses to run under a read-only session.
Idempotent: the target table is truncated before each load so reruns are
deterministic.
"""

from datetime import date, datetime
from decimal import Decimal

from psycopg2.extras import execute_values

from libs.engine import schema as schema_mod
from libs.engine.schema import ddl_for_contract


def _to_python(v):
    if v is None:
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date | datetime):
        return v
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _coerce(v, coltype: str):
    v = _to_python(v)
    if v is None:
        return None
    if coltype == "integer":
        return int(Decimal(str(v)))
    if coltype == "decimal":
        return Decimal(str(v))
    if coltype == "date":
        return datetime.strptime(str(v), "%Y-%m-%d").date() if isinstance(v, str) else v
    if coltype == "timestamp":
        if isinstance(v, str):
            s = v.strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
        return v
    if coltype == "boolean":
        return str(v).strip().lower() in ("true", "1", "t", "yes")
    return str(v)


def load_expected_rows(conn, contract, expected_df, dialect: str = "postgres") -> int:
    """Create target table per contract, truncate, bulk-insert expected rows.

    Raises PermissionError if the session is read-only (e.g. recon_ro creds).
    MySQL targets rely on the write role's grants — the INSERT itself is denied
    for read-only users.
    """
    if dialect == "snowflake":
        raise PermissionError("Snowflake loading is disabled; PROD-05 is verification-only")
    if dialect not in ("postgres", "mysql"):
        raise ValueError(f"Unsupported loader dialect: {dialect}")
    cur = conn.cursor()
    if dialect == "postgres":
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone()[0] == "on":
            cur.close()
            raise PermissionError(
                "refusing to load under a read-only session (loader requires recon_rw)"
            )

    qualified = (
        f"{schema_mod.ident(contract.schema, dialect)}.{schema_mod.ident(contract.table, dialect)}"
    )
    cur.execute(ddl_for_contract(contract, dialect))
    cur.execute(f"TRUNCATE TABLE {qualified}")

    colnames = [c["name"] for c in contract.columns]
    coltypes = {c["name"]: c["type"] for c in contract.columns}
    rows = [
        tuple(_coerce(row[c], coltypes[c]) for c in colnames) for _, row in expected_df.iterrows()
    ]
    cols_sql = ", ".join(schema_mod.ident(c, dialect) for c in colnames)
    if dialect == "mysql":
        placeholders = ", ".join(["%s"] * len(colnames))
        cur.executemany(f"INSERT INTO {qualified} ({cols_sql}) VALUES ({placeholders})", rows)
    else:
        execute_values(
            cur,
            f"INSERT INTO {qualified} ({cols_sql}) VALUES %s",
            rows,
        )
    conn.commit()
    n = len(rows)
    cur.close()
    return n
