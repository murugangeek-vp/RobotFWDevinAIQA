"""Snowflake target adapter + loader writer.

Real accounts connect via snowflake.connector.connect; `fake: true` runs the
same code path against fakesnow (DuckDB-backed emulation) — the demo/local
mode until real credentials arrive. `fake_db_path` pins the emulated
warehouse to a file so loader and verifier connections share state.
"""

import pandas as pd

from libs.adapters.base import TargetAdapter
from libs.adapters.registry import register_target, register_writer
from libs.engine.schema import ident

# fakesnow db_path is a DIRECTORY — it creates one .db file per database.
_DEFAULT_FAKE_DB = "results/fakesnow"

# One FakeSnow per db_path per process — the emulated "warehouse". Every fake
# connection (loader, verifier, defect seeder) shares it, exactly like real
# connections sharing a Snowflake account. A fakesnow.patch() context can't
# be used here: it closes the shared duckdb catalog on exit, killing every
# connection made under it.
_FAKE_INSTANCES: dict = {}


def _fake_snow(db_path):
    from pathlib import Path

    import fakesnow

    key = str(db_path or ":memory:")
    fs = _FAKE_INSTANCES.get(key)
    if fs is None:
        if db_path:
            Path(db_path).mkdir(parents=True, exist_ok=True)
        fs = fakesnow.FakeSnow(
            create_database_on_connect=True,
            create_schema_on_connect=True,
            db_path=db_path,
        )
        _FAKE_INSTANCES[key] = fs
    return fs


def _connect(
    account=None,
    user=None,
    password=None,
    warehouse=None,
    database=None,
    schema="PUBLIC",
    role=None,
    fake=False,
    fake_db_path=None,
    **_,
):
    if fake:
        return _fake_snow(fake_db_path or _DEFAULT_FAKE_DB).connect(
            account=account,
            user=user,
            password=password,
            warehouse=warehouse,
            database=database,
            schema=schema,
            role=role,
        )
    import snowflake.connector

    if not role:
        raise ValueError("An explicit least-privilege Snowflake role is required")
    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        database=database,
        schema=schema,
        role=role,
        login_timeout=30,
        network_timeout=120,
        client_prefetch_threads=1,
        session_parameters={
            "STATEMENT_TIMEOUT_IN_SECONDS": 300,
            "QUERY_TAG": "bank_reconciliation",
        },
    )


@register_target("snowflake")
class SnowflakeTarget(TargetAdapter):
    """Read-only verification adapter for Snowflake targets.

    SELECT-only by usage; on real accounts connect with a SELECT-only role.
    In fake mode (fakesnow/DuckDB) there is no role enforcement — that path
    is for local demos only.
    """

    dialect = "snowflake"

    def __init__(
        self,
        account=None,
        user=None,
        password=None,
        warehouse=None,
        database=None,
        schema="PUBLIC",
        role=None,
        fake=False,
        fake_db_path=None,
    ):
        self.schema_name = schema or "PUBLIC"
        self.conn = _connect(
            account=account,
            user=user,
            password=password,
            warehouse=warehouse,
            database=database,
            schema=self.schema_name,
            role=role,
            fake=fake,
            fake_db_path=fake_db_path or _DEFAULT_FAKE_DB,
        )

    @staticmethod
    def _names(cur) -> list:
        return [getattr(d, "name", None) or d[0] for d in cur.description]

    def _qualified(self, table: str) -> str:
        # Quoted identifiers keep contract-case names resolvable on real
        # Snowflake (unquoted names fold to UPPER) and on fakesnow/DuckDB.
        from libs.engine.schema import ident

        return f"{ident(self.schema_name, self.dialect)}.{ident(table, self.dialect)}"

    def read_table(self, table: str, columns: "list | None" = None):
        cols = ", ".join(ident(c, self.dialect) for c in columns) if columns else "*"
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols} FROM {self._qualified(table)} ORDER BY 1")
        rows = cur.fetchall()
        names = self._names(cur)
        cur.close()
        return pd.DataFrame(rows, columns=names, dtype=object)

    def read_chunks(
        self,
        table: str,
        columns: "list | None" = None,
        keys: "list | None" = None,
        size: int = 50000,
    ):
        cols = ", ".join(ident(c, self.dialect) for c in columns) if columns else "*"
        if size < 1:
            raise ValueError("batch size must be positive")
        order = ", ".join(ident(k, self.dialect) for k in keys) if keys else "1"
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT {cols} FROM {self._qualified(table)} ORDER BY {order}")
            names = self._names(cur)
            while True:
                rows = cur.fetchmany(size)
                if not rows:
                    break
                yield pd.DataFrame(rows, columns=names, dtype=object)
        finally:
            cur.close()

    def query(self, sql: str, params: tuple = ()):
        from sqlglot import exp, parse

        statements = parse(sql, read="snowflake")
        if (
            len(statements) != 1
            or not isinstance(statements[0], exp.Select)
            or statements[0].find(exp.Into)
        ):
            raise ValueError("Verification accepts one SELECT statement only")
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params or None)
            return pd.DataFrame(cur.fetchall(), columns=self._names(cur), dtype=object)
        finally:
            cur.close()

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        cur.execute(f"SELECT count(*) FROM {self._qualified(table)}")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def schema(self, table: str) -> list:
        from libs.engine.schema import fetch_db_schema

        return fetch_db_schema(self.conn, self.schema_name, table, dialect=self.dialect)

    def primary_key(self, table: str) -> list:
        from libs.engine.schema import fetch_primary_key

        if type(self.conn).__module__.startswith("fakesnow"):
            return fetch_primary_key(self.conn, self.schema_name, table, dialect=self.dialect)
        cur = self.conn.cursor()
        try:
            cur.execute(f"SHOW PRIMARY KEYS IN TABLE {self._qualified(table)}")
            rows = [dict(zip(self._names(cur), row, strict=True)) for row in cur.fetchall()]
            return [r["column_name"] for r in sorted(rows, key=lambda r: r["key_sequence"])]
        finally:
            cur.close()

    def is_closed(self) -> bool:
        fn = getattr(self.conn, "is_closed", None)
        if callable(fn):
            return bool(fn())
        return bool(getattr(self.conn, "closed", False))

    def close(self):
        self.conn.close()


@register_writer("snowflake")
def connect_rw_snowflake(
    account=None,
    user=None,
    password=None,
    warehouse=None,
    database=None,
    schema="PUBLIC",
    role=None,
    fake=False,
    fake_db_path=None,
):
    """Loader connection. Real Snowflake: a write-capable role; fake mode has
    no role enforcement."""
    return _connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        database=database,
        schema=schema,
        role=role,
        fake=fake,
        fake_db_path=fake_db_path or _DEFAULT_FAKE_DB,
    )
