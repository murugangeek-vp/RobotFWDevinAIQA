"""PROD-06: MySQL target adapter.

Verification adapter connects as the recon_ro-equivalent user (SELECT-only
grants) with a read-only session. `schema`/`primary_key` read
information_schema — contract types map via libs.engine.schema.TYPE_ALIASES
("mysql" dialect). In MySQL, `schema` in the contract is the database name.
"""

import pandas as pd
import pymysql

from libs.adapters.base import TargetAdapter
from libs.adapters.registry import register_target, register_writer


@register_target("mysql")
class MysqlTarget(TargetAdapter):
    """Read-only verification adapter for MySQL (SELECT-only grants)."""

    dialect = "mysql"

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema: "str | None" = None,
    ):
        self.schema_name = schema or database
        self.conn = pymysql.connect(
            host=host,
            port=int(port),
            database=database,
            user=user,
            password=password,
            autocommit=True,
        )
        cur = self.conn.cursor()
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.close()

    def _table(self, table: str) -> str:
        return f"`{self.schema_name}`.`{table}`"

    def read_table(self, table: str, columns: "list | None" = None):
        cols = ", ".join(f"`{c}`" for c in columns) if columns else "*"
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols} FROM {self._table(table)} ORDER BY 1")
        rows = cur.fetchall()
        names = [d[0] for d in cur.description]
        cur.close()
        return pd.DataFrame(rows, columns=names)

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        cur.execute(f"SELECT count(*) FROM {self._table(table)}")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def schema(self, table: str) -> list:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (self.schema_name, table),
        )
        rows = [(r[0], r[1], r[2] == "YES") for r in cur.fetchall()]
        cur.close()
        return rows

    def primary_key(self, table: str) -> list:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.key_column_usage
            WHERE table_schema = %s AND table_name = %s
              AND constraint_name = 'PRIMARY'
            ORDER BY ordinal_position
            """,
            (self.schema_name, table),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows

    def query(self, sql: str, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def _qualified(self, table: str) -> str:
        from libs.engine.schema import ident

        return f"{ident(self.schema_name, 'mysql')}.{ident(table, 'mysql')}"

    def _fetch(self, sql: str, max_rows: int) -> list:
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            return list(cur.fetchmany(max_rows))
        finally:
            cur.close()

    def is_closed(self) -> bool:
        return not self.conn.open

    def close(self):
        self.conn.close()


@register_writer("mysql")
def connect_mysql_rw(host: str, port: int, database: str, user: str, password: str):
    """Read-write connection for the loader only (recon_rw-equivalent grants)."""
    return pymysql.connect(
        host=host, port=int(port), database=database, user=user, password=password
    )
