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
    """Read a PostgreSQL table as the migration source (SELECT-only session).

    Rows stream through a named server-side cursor so million-row sources
    stay flat in memory; the optional `where` fragment is vetted read-only.
    Config keys: host, port, database, user, password, schema, table,
    columns, where, batch_size.
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
    ):
        _ident(schema, "schema")
        _ident(table, "table")
        if int(batch_size) < 1:
            raise ValueError("batch_size must be positive")
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
        self.schema_name = _ident(schema, "schema")
        self.table = _ident(table, "table")
        self.columns = [_ident(c, "column") for c in columns] if columns else None
        self.batch_size = int(batch_size or _DEFAULT_BATCH)
        if where:
            from libs.excelrules.validators import where_is_safe

            if not where_is_safe(where):
                raise ValueError(f"unsafe source WHERE fragment: {where!r}")
        self.where = where

    def _select_sql(self, count_only: bool = False) -> str:
        cols = (
            "count(*)"
            if count_only
            else (", ".join(f'"{c}"' for c in self.columns) if self.columns else "*")
        )
        sql = f'SELECT {cols} FROM "{self.schema_name}"."{self.table}"'
        if self.where:
            sql += f" WHERE ({self.where})"
        return sql

    def read_batch(self, **kwargs):
        frames = list(self.read_chunks())
        if not frames:
            return pd.DataFrame(columns=self.columns or [])
        return pd.concat(frames, ignore_index=True)

    def read_chunks(self, size: int = 0):
        cur = self.conn.cursor(name="recon_src")
        cur.itersize = int(size or self.batch_size)
        try:
            cur.execute(self._select_sql())
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
        cur.execute(self._select_sql(count_only=True))
        n = cur.fetchone()[0]
        cur.close()
        return n

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
