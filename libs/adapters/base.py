import re
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

# MIG-P5: batch/delta verification — batch ids come from ops tooling; allow only
# a closed character set so a value can never widen into SQL syntax.
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class SourceAdapter(ABC):
    """Stable interface for source systems (CSV now; S3/API/Dataiku later)."""

    @abstractmethod
    def read_batch(self, **kwargs):
        """Return a pandas DataFrame of source rows."""

    @abstractmethod
    def row_count(self) -> int:
        """Number of source records."""

    @abstractmethod
    def schema(self) -> list:
        """List of source column names in order."""

    @abstractmethod
    def close(self):
        """Release resources."""


class TargetAdapter(ABC):
    """Stable interface for targets. Verification paths must be read-only.

    Concrete adapters must also expose `.conn` — the underlying DBAPI
    connection used by schema/PK introspection helpers (read-only session) —
    and set `.dialect` to a key in libs.engine.schema.TYPE_ALIASES.
    """

    conn: Any
    dialect: str = "unknown"

    @abstractmethod
    def read_table(self, table: str, columns: "list | None" = None):
        """Return a pandas DataFrame of target rows."""

    @abstractmethod
    def row_count(self, table: str) -> int:
        """Number of records in the target table."""

    @abstractmethod
    def schema(self, table: str) -> list:
        """[(column_name, data_type, is_nullable), ...]"""

    @abstractmethod
    def primary_key(self, table: str) -> list:
        """Primary-key column names in key order (empty if none)."""

    @abstractmethod
    def is_closed(self) -> bool:
        """True when the underlying connection is dead."""

    @abstractmethod
    def close(self):
        """Release resources."""

    # ----- MIG-04/05: fixed-shape pushdown queries ---------------------------
    # SQL below is built only from identifier-validated names and a closed set of
    # aggregate functions; callers never supply SQL text. Adapters opt in by
    # implementing _qualified() and _fetch().

    PUSHDOWN_MAX_ROWS = 10000
    _AGGREGATES = {
        "count": "COUNT(*)",
        "sum": "SUM({c})",
        "min": "MIN({c})",
        "max": "MAX({c})",
        "null_count": "COUNT(*) - COUNT({c})",
        "count_distinct": "COUNT(DISTINCT {c})",
    }

    def _qualified(self, table: str) -> str:
        raise NotImplementedError(f"{type(self).__name__} does not support pushdown queries")

    def _fetch(self, sql: str, max_rows: int) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support pushdown queries")

    def _bounded(self, sql: str) -> list:
        rows = self._fetch(sql, self.PUSHDOWN_MAX_ROWS + 1)
        if len(rows) > self.PUSHDOWN_MAX_ROWS:
            raise ValueError(f"pushdown result exceeds {self.PUSHDOWN_MAX_ROWS} rows")
        return [tuple(r) for r in rows]

    # ----- MIG-P5: batch/delta filtering -----------------------------------------

    def _batch_frag(self, batch, alias: str = "") -> str:
        """'ch.batch_col = 'id'' fragment for WHERE/AND — or '' when no batch filter."""
        if not batch:
            return ""
        col, value = batch
        if not BATCH_ID_RE.fullmatch(str(value)):
            raise ValueError("batch id fails the allowed-pattern check")
        from libs.engine.schema import ident

        return f"{alias}{ident(col, self.dialect)} = '{value}'"

    def _batch_where(self, batch, alias: str = "") -> str:
        frag = self._batch_frag(batch, alias)
        return f" WHERE {frag}" if frag else ""

    def _batch_and(self, batch, alias: str = "") -> str:
        frag = self._batch_frag(batch, alias)
        return f" AND {frag}" if frag else ""

    def row_count_filtered(self, table: str, batch=None) -> int:
        """COUNT(*) restricted to one load batch (delta verification)."""
        sql = f"SELECT COUNT(*) FROM {self._qualified(table)}{self._batch_where(batch)}"
        return int(self._bounded(sql)[0][0])

    def filtered_rows(self, table: str, columns: list, batch=None):
        """Contract columns restricted to one load batch — bounded like read_table."""
        from libs.engine.schema import ident

        cols = ", ".join(ident(c, self.dialect) for c in columns)
        sql = f"SELECT {cols} FROM {self._qualified(table)}{self._batch_where(batch)}"
        bound = getattr(self, "max_rows", 100000)
        rows = self._fetch(sql, bound + 1)
        if len(rows) > bound:
            raise ValueError("filtered result exceeds max_rows; reconciliation was not performed")
        return pd.DataFrame(rows, columns=columns)

    def aggregate(
        self,
        table: str,
        func: str,
        column: "str | None" = None,
        group_by: "list | None" = None,
        batch=None,
    ) -> list:
        """[(group values..., aggregate)] for one control; no rows leave the target."""
        from libs.engine.schema import ident

        if func not in self._AGGREGATES:
            raise ValueError(f"unsupported aggregate {func!r}")
        if (func == "count") != (column is None):
            raise ValueError("count takes no column; other aggregates require one")
        expr = self._AGGREGATES[func].format(c=ident(column, self.dialect) if column else "")
        groups = [ident(g, self.dialect) for g in group_by or []]
        select = ", ".join([*groups, expr])
        sql = f"SELECT {select} FROM {self._qualified(table)}{self._batch_where(batch)}"
        if groups:
            sql += " GROUP BY " + ", ".join(groups)
        return self._bounded(sql)

    def orphans(
        self,
        table: str,
        column: str,
        parent_table: str,
        parent_column: str,
        samples: int = 5,
        batch=None,
    ) -> tuple:
        """(orphan_count, sample orphan values): non-null child values with no parent."""
        from libs.engine.schema import ident

        c, p = ident(column, self.dialect), ident(parent_column, self.dialect)
        where = (
            f"FROM {self._qualified(table)} ch WHERE ch.{c} IS NOT NULL"
            f"{self._batch_and(batch, 'ch.')} AND NOT EXISTS "
            f"(SELECT 1 FROM {self._qualified(parent_table)} pa WHERE pa.{p} = ch.{c})"
        )
        count = int(self._bounded(f"SELECT COUNT(*) {where}")[0][0])
        n = int(samples)
        sample = [r[0] for r in self._bounded(f"SELECT ch.{c} {where} ORDER BY 1 LIMIT {n}")]
        return count, sample

    # ----- MIG-P2: bucketed fingerprint compare -------------------------------

    def bucket_checksums(self, table: str, contract, buckets: int, batch=None) -> dict:
        """{bucket: (row_count, fingerprint_sum)} — generated SQL, nothing else runs."""
        from libs.migration.fingerprint import bucket_checksum_sql

        sql = bucket_checksum_sql(contract, self.dialect, self._qualified(table), buckets)
        frag = self._batch_frag(batch)
        if frag:
            sql = sql.replace(" GROUP BY ", f" WHERE {frag} GROUP BY ")
        return {int(b): (int(n), int(fp)) for b, n, fp in self._bounded(sql)}

    def bucket_rows(self, table: str, contract, buckets: int, bucket: int, batch=None):
        """All contract columns for one bucket — drill-down for a checksum diff."""
        from libs.engine.schema import ident
        from libs.migration.fingerprint import bucket_sql

        names = [c["name"] for c in contract.columns]
        cols = ", ".join(ident(c, self.dialect) for c in names)
        where = bucket_sql(contract, self.dialect, buckets)
        sql = (
            f"SELECT {cols} FROM {self._qualified(table)} WHERE {where} = {int(bucket)}"
            f"{self._batch_and(batch)}"
        )
        return pd.DataFrame(self._bounded(sql), columns=names)
