import re

import pandas as pd
import psycopg2

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source

_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_DEFAULT_BATCH = 50000


def _ident(name, what):
    if not name or not _IDENT.match(str(name)):
        raise ValueError(f"unsafe {what} identifier: {name!r}")
    return str(name)


@register_source("postgres")
class PostgresSource(SourceAdapter):
    dialect = "postgres"

    """Read a PostgreSQL table as the migration source (SELECT-only session).

    Rows stream through a named server-side cursor so million-row sources
    stay flat in memory; the optional `where` fragment is vetted read-only.
    Config keys: host, port, database, user, password, schema, table,
    columns, where, batch_size, sslmode, sslrootcert, snapshot (exported
    pg snapshot id pinned via SET TRANSACTION SNAPSHOT), snapshot_column +
    snapshot_value (bound watermark predicate for non-pg-exportable sources).
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        table: str,
        schema: str = "public",
        columns: "list | None" = None,
        where: "str | None" = None,
        batch_size: int = _DEFAULT_BATCH,
        sslmode: str | None = None,
        sslrootcert: str | None = None,
        snapshot: "str | None" = None,
        snapshot_column: "str | None" = None,
        snapshot_value: "str | None" = None,
    ):
        _ident(schema, "schema")
        _ident(table, "table")
        if int(batch_size) < 1:
            raise ValueError("batch_size must be positive")
        # Validate everything before opening a connection — fail fast.
        if snapshot is not None and not re.fullmatch(
            r"[0-9A-Fa-f]+(-[0-9A-Fa-f]+)+", str(snapshot)
        ):
            # pg_export_snapshot() ids look like '0000000A-00000C7B-1'.
            raise ValueError(f"malformed exported snapshot id: {snapshot!r}")
        self.snapshot_column = (
            _ident(snapshot_column, "snapshot column") if snapshot_column else None
        )
        if self.snapshot_column and snapshot_value in (None, ""):
            raise ValueError("snapshot_column requires a snapshot_value")
        self._params = (snapshot_value,) if self.snapshot_column else None
        self._pinned = snapshot is not None
        if where:
            from libs.excelrules.validators import where_is_safe

            if not where_is_safe(where):
                raise ValueError(f"unsafe source WHERE fragment: {where!r}")
        self.where = where
        self.conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=15,
            options="-c statement_timeout=300000",
            sslmode=sslmode
            or ("prefer" if host in ("localhost", "127.0.0.1", "::1") else "verify-full"),
            **({"sslrootcert": sslrootcert} if sslrootcert else {}),
        )
        # readonly session; autocommit stays off — the named server-side
        # cursor in read_chunks needs an open transaction.
        self.conn.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
        if snapshot is not None:
            # Set as the first statement so the whole session reads the same
            # source snapshot the exporter pinned.
            cur = self.conn.cursor()
            cur.execute(f"SET TRANSACTION SNAPSHOT '{snapshot}'")
            cur.close()
        self.schema_name = _ident(schema, "schema")
        self.table = _ident(table, "table")
        self.columns = [_ident(c, "column") for c in columns] if columns else None
        self.batch_size = int(batch_size or _DEFAULT_BATCH)

    def _select_sql(self, count_only: bool = False) -> str:
        cols = (
            "count(*)"
            if count_only
            else (", ".join(f'"{c}"' for c in self.columns) if self.columns else "*")
        )
        sql = f'SELECT {cols} FROM "{self.schema_name}"."{self.table}"'
        clauses = []
        if self.where:
            clauses.append(f"({self.where})")
        if self.snapshot_column:
            clauses.append(f'"{self.snapshot_column}" <= %s')
        return sql + (" WHERE " + " AND ".join(clauses) if clauses else "")

    def read_batch(self, **kwargs):
        frames = list(self.read_chunks())
        if not frames:
            return pd.DataFrame(columns=self.columns or [])
        return pd.concat(frames, ignore_index=True)

    def read_chunks(self, size: int = 0):
        cur = self.conn.cursor(name="recon_src")
        cur.itersize = int(size or self.batch_size)
        try:
            cur.execute(self._select_sql(), self._params)
            # server-side cursors only populate .description after a fetch
            names = None
            while True:
                rows = cur.fetchmany(cur.itersize)
                if not rows:
                    break
                if names is None:
                    names = [d.name for d in cur.description]
                yield pd.DataFrame(rows, columns=names, dtype=object)
        finally:
            cur.close()

    def row_count(self) -> int:
        cur = self.conn.cursor()
        cur.execute(self._select_sql(count_only=True), self._params)
        n = cur.fetchone()[0]
        cur.close()
        return n

    def post_snapshot_count(self) -> "int | None":
        # A session pinned to an exported snapshot cannot see post-cut
        # arrivals at all — counting them here would return a wrong 0.
        if not self.snapshot_column or self._pinned:
            return None
        clauses = [f'"{self.snapshot_column}" > %s']
        if self.where:
            clauses.insert(0, f"({self.where})")
        sql = (
            f'SELECT count(*) FROM "{self.schema_name}"."{self.table}"'
            + " WHERE "
            + " AND ".join(clauses)
        )
        cur = self.conn.cursor()
        try:
            cur.execute(sql, self._params)
            return int(cur.fetchone()[0])
        finally:
            cur.close()

    def schema(self) -> list:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (self.schema_name, self.table),
        )
        cols = [r[0] for r in cur.fetchall()]
        cur.close()
        return cols

    def close(self):
        self.conn.close()
