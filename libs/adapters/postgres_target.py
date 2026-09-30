import pandas as pd
import psycopg2

from libs.adapters.base import TargetAdapter


class PostgresTarget(TargetAdapter):
    """Read-only verification adapter. Connect as recon_ro (SELECT-only role)."""

    def __init__(self, host: str, port: int, database: str, user: str,
                 password: str, schema: str = "public"):
        self.schema = schema
        self.conn = psycopg2.connect(
            host=host, port=port, dbname=database, user=user, password=password)
        self.conn.set_session(readonly=True, autocommit=True)

    def read_table(self, table: str, columns: list = None):
        cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        cur = self.conn.cursor()
        cur.execute(f"SELECT {cols} FROM {self.schema}.{table} ORDER BY 1")
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
        cur.close()
        return pd.DataFrame(rows, columns=names)

    def row_count(self, table: str) -> int:
        cur = self.conn.cursor()
        cur.execute(f"SELECT count(*) FROM {self.schema}.{table}")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def schema_info(self, table: str) -> list:
        from libs.engine.schema import fetch_db_schema
        return fetch_db_schema(self.conn, self.schema, table)

    def schema(self, table: str) -> list:
        return self.schema_info(table)

    def query(self, sql: str, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def close(self):
        self.conn.close()


def connect_rw(host: str, port: int, database: str, user: str, password: str):
    """Read-write connection for the loader only (recon_rw role)."""
    return psycopg2.connect(host=host, port=port, dbname=database,
                            user=user, password=password)
