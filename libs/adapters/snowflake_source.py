import pandas as pd

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source
from libs.adapters.snowflake_target import SnowflakeTarget
from libs.engine.schema import ident
from libs.excelrules.validators import where_is_safe


@register_source("snowflake")
class SnowflakeSource(SourceAdapter):
    def __init__(
        self,
        table,
        database,
        account=None,
        user=None,
        password=None,
        warehouse=None,
        schema="PUBLIC",
        role=None,
        columns=None,
        where=None,
        fake=False,
        fake_db_path=None,
        batch_size=5000,
    ):
        if where and not where_is_safe(where):
            raise ValueError("Unsafe source WHERE fragment")
        self.target = SnowflakeTarget(
            account, user, password, warehouse, database, schema, role, fake, fake_db_path
        )
        self.table, self.columns, self.where = table, columns, where
        self.batch_size = batch_size

    def _select(self, count=False):
        cols = (
            "count(*)"
            if count
            else ", ".join(ident(c, "snowflake") for c in self.columns or []) or "*"
        )
        sql = f"SELECT {cols} FROM {self.target._qualified(self.table)}"
        return sql + (f" WHERE ({self.where})" if self.where else "")

    def read_chunks(self, size=0):
        size = size or self.batch_size
        if size < 1:
            raise ValueError("batch size must be positive")
        cur = self.target.conn.cursor()
        try:
            cur.execute(self._select())
            names = self.target._names(cur)
            while rows := cur.fetchmany(size):
                yield pd.DataFrame(rows, columns=names, dtype=object)
        finally:
            cur.close()

    def read_batch(self, **kwargs):
        chunks = list(self.read_chunks())
        return (
            pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=self.columns)
        )

    def row_count(self):
        return int(self.target.query(self._select(count=True)).iloc[0, 0])

    def schema(self):
        return [row[0] for row in self.target.schema(self.table)]

    def close(self):
        self.target.close()
