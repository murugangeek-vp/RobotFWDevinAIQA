import pandas as pd
import psycopg2

from libs.adapters.base import TargetAdapter
from libs.adapters.registry import register_target, register_writer


@register_target("postgres")
class PostgresTarget(TargetAdapter):
    """Read-only verification adapter. Connect as recon_ro (SELECT-only role)."""

    dialect = "postgres"

    def __init__(
        self, host: str, port: int, database: str, user: str, password: str, schema: str = "public"
    ):
        self.schema_name = schema
        self.conn = psycopg2.connect(
            host=host, port=port, dbname=database, user=user, password=password
        )
        self.conn.set_session(readonly=True, autocommit=True)

    def read_table(self, table: str, columns: "list | None" = None):
        cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols} FROM {self.schema_name}.{table} ORDER BY 1")
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
        cur.close()
        return pd.DataFrame(rows, columns=names)

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        cur.execute(f"SELECT count(*) FROM {self.schema_name}.{table}")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def schema(self, table: str) -> list:
        from libs.engine.schema import fetch_db_schema

        return fetch_db_schema(self.conn, self.schema_name, table)

    def primary_key(self, table: str) -> list:
        from libs.engine.schema import fetch_primary_key

        return fetch_primary_key(self.conn, self.schema_name, table)

    def is_closed(self) -> bool:
        return bool(self.conn.closed)

    def query(self, sql: str, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def close(self):
        self.conn.close()


@register_writer("postgres")
def connect_rw(host: str, port: int, database: str, user: str, password: str):
    """Read-write connection for the loader only (recon_rw role)."""
    return psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)
