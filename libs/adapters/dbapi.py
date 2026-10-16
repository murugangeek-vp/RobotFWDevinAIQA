"""Shared DB-API plumbing for the mysql / oracle / mssql adapters.

Each dialect supplies ``_connect`` (lazy driver import), ``_cursor``
(streaming cursor — e.g. pymysql SSCursor) and ``_default_schema``.
Everything else — identifier vetting, safe WHERE fragments, snapshot-column
watermarks, bounded-memory chunking, read-only ``query`` vetting and catalog
introspection — is identical across drivers and lives here once.

Unlike Postgres there is no exported-snapshot primitive on these engines:
consistency comes from ``snapshot_column`` watermarking plus a least-
privilege read-only role on the account (grants, not session flags, are the
enforcement — ``query()`` is additionally restricted to a single SELECT).
"""

import re

import pandas as pd

from libs.adapters.base import SourceAdapter, TargetAdapter
from libs.engine.schema import fetch_db_schema, fetch_primary_key, ident

_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_DEFAULT_BATCH = 50000

# sqlglot parse dialect per adapter dialect (for the read-only query guard)
_SQLGLOT_READ = {"mysql": "mysql", "oracle": "oracle", "mssql": "tsql"}


def _vident(name, what):
    if not name or not _IDENT.match(str(name)):
        raise ValueError(f"unsafe {what} identifier: {name!r}")
    return str(name)


def _vet_select(sql: str, dialect: str):
    """Reject anything but a single SELECT (mirrors SnowflakeTarget.query)."""
    from sqlglot import exp, parse

    statements = parse(sql, read=_SQLGLOT_READ.get(dialect))
    if (
        len(statements) != 1
        or not isinstance(statements[0], exp.Select)
        or statements[0].find(exp.Into)
    ):
        raise ValueError("Verification accepts one SELECT statement only")


class DbapiSource(SourceAdapter):
    """Read a configured table as the migration source (SELECT-only).

    Config keys follow PostgresSource: host, port, database, user, password,
    schema, table, columns, where, batch_size, snapshot_column +
    snapshot_value (bound watermark predicate). ``snapshot`` (pg exported
    snapshot) is rejected — unsupported on these engines.
    """

    dialect = ""
    PARAM = "%s"

    def _connect(self, host, port, database, user, password, **kw):
        raise NotImplementedError

    def _cursor(self):
        return self.conn.cursor()

    @staticmethod
    def _names(cur) -> list:
        return [getattr(d, "name", None) or d[0] for d in cur.description]

    def _default_schema(self, user, database):
        return "public"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        table: str,
        schema: "str | None" = None,
        columns: "list | None" = None,
        where: "str | None" = None,
        batch_size: int = _DEFAULT_BATCH,
        snapshot: "str | None" = None,
        snapshot_column: "str | None" = None,
        snapshot_value: "str | None" = None,
        **kw,
    ):
        # Validate everything before opening a connection — fail fast.
        if snapshot is not None:
            raise ValueError(
                "exported snapshots are postgres-only; use snapshot_column watermarking"
            )
        _vident(table, "table")
        if int(batch_size) < 1:
            raise ValueError("batch_size must be positive")
        self.snapshot_column = (
            _vident(snapshot_column, "snapshot column") if snapshot_column else None
        )
        if self.snapshot_column and snapshot_value in (None, ""):
            raise ValueError("snapshot_column requires a snapshot_value")
        self._params = (snapshot_value,) if self.snapshot_column else None
        if where:
            from libs.excelrules.validators import where_is_safe

            if not where_is_safe(where):
                raise ValueError(f"unsafe source WHERE fragment: {where!r}")
        self.where = where
        self.conn = self._connect(
            host=host, port=port, database=database, user=user, password=password, **kw
        )
        self.schema_name = _vident(schema or self._default_schema(user, database), "schema")
        self.table = _vident(table, "table")
        self.columns = [_vident(c, "column") for c in columns] if columns else None
        self.batch_size = int(batch_size or _DEFAULT_BATCH)

    def _qualified(self) -> str:
        return f"{ident(self.schema_name, self.dialect)}.{ident(self.table, self.dialect)}"

    def _select_sql(self, count_only: bool = False) -> str:
        cols = (
            "count(*)"
            if count_only
            else (", ".join(ident(c, self.dialect) for c in self.columns) if self.columns else "*")
        )
        sql = f"SELECT {cols} FROM {self._qualified()}"
        clauses = []
        if self.where:
            clauses.append(f"({self.where})")
        if self.snapshot_column:
            clauses.append(f"{ident(self.snapshot_column, self.dialect)} <= {self.PARAM}")
        return sql + (" WHERE " + " AND ".join(clauses) if clauses else "")

    def read_chunks(self, size: int = 0):
        size = int(size or self.batch_size)
        if size < 1:
            raise ValueError("batch size must be positive")
        cur = self._cursor()
        try:
            cur.execute(self._select_sql(), self._params)
            names = self._names(cur)
            while rows := cur.fetchmany(size):
                # pyodbc returns Row objects pandas treats as scalars —
                # normalize to tuples so frame columns line up for all drivers.
                yield pd.DataFrame([tuple(r) for r in rows], columns=names, dtype=object)
        finally:
            cur.close()

    def read_batch(self, **kwargs):
        frames = list(self.read_chunks())
        if not frames:
            return pd.DataFrame(columns=self.columns or [])
        return pd.concat(frames, ignore_index=True)

    def row_count(self) -> int:
        cur = self.conn.cursor()
        try:
            cur.execute(self._select_sql(count_only=True), self._params)
            return int(cur.fetchone()[0])
        finally:
            cur.close()

    def post_snapshot_count(self) -> "int | None":
        if not self.snapshot_column:
            return None
        clauses = [f"{ident(self.snapshot_column, self.dialect)} > {self.PARAM}"]
        if self.where:
            clauses.insert(0, f"({self.where})")
        sql = f"SELECT count(*) FROM {self._qualified()} WHERE " + " AND ".join(clauses)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, self._params)
            return int(cur.fetchone()[0])
        finally:
            cur.close()

    def schema(self) -> list:
        return [
            row[0]
            for row in fetch_db_schema(
                self.conn, self.schema_name, self.table, dialect=self.dialect
            )
        ]

    def close(self):
        self.conn.close()


class DbapiTarget(TargetAdapter):
    """Read-only verification adapter for a DB-API target.

    Least privilege is enforced by the account's grants on these engines —
    connect with a SELECT-only role; ``query()`` additionally rejects
    anything but a single SELECT statement.
    """

    dialect = ""
    PARAM = "%s"

    def _connect(self, host, port, database, user, password, **kw):
        raise NotImplementedError

    def _cursor(self):
        return self.conn.cursor()

    def _default_schema(self, user, database):
        return "public"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema: "str | None" = None,
        **kw,
    ):
        self.schema_name = schema or self._default_schema(user, database)
        self.conn = self._connect(
            host=host, port=port, database=database, user=user, password=password, **kw
        )
        self._closed = False

    @staticmethod
    def _names(cur) -> list:
        return [getattr(d, "name", None) or d[0] for d in cur.description]

    def _qualified(self, table: str) -> str:
        return f"{ident(self.schema_name, self.dialect)}.{ident(table, self.dialect)}"

    def read_table(self, table: str, columns: "list | None" = None):
        cols = ", ".join(ident(c, self.dialect) for c in columns) if columns else "*"
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT {cols} FROM {self._qualified(table)} ORDER BY 1")
            return pd.DataFrame(
                [tuple(r) for r in cur.fetchall()], columns=self._names(cur), dtype=object
            )
        finally:
            cur.close()

    def read_chunks(
        self,
        table: str,
        columns: "list | None" = None,
        keys: "list | None" = None,
        size: int = _DEFAULT_BATCH,
    ):
        if size < 1:
            raise ValueError("batch size must be positive")
        cols = ", ".join(ident(c, self.dialect) for c in columns) if columns else "*"
        order = ", ".join(ident(k, self.dialect) for k in keys) if keys else "1"
        cur = self._cursor()
        try:
            cur.execute(f"SELECT {cols} FROM {self._qualified(table)} ORDER BY {order}")
            names = self._names(cur)
            while rows := cur.fetchmany(size):
                # pyodbc returns Row objects pandas treats as scalars —
                # normalize to tuples so frame columns line up for all drivers.
                yield pd.DataFrame([tuple(r) for r in rows], columns=names, dtype=object)
        finally:
            cur.close()

    def query(self, sql: str, params: tuple = ()):
        _vet_select(sql, self.dialect)
        cur = self.conn.cursor()
        try:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return pd.DataFrame(
                [tuple(r) for r in cur.fetchall()], columns=self._names(cur), dtype=object
            )
        finally:
            cur.close()

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT count(*) FROM {self._qualified(table)}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()

    def schema(self, table: str) -> list:
        return fetch_db_schema(self.conn, self.schema_name, table, dialect=self.dialect)

    def primary_key(self, table: str) -> list:
        return fetch_primary_key(self.conn, self.schema_name, table, dialect=self.dialect)

    def is_closed(self) -> bool:
        return self._closed

    def close(self):
        self._closed = True
        self.conn.close()
