from abc import ABC, abstractmethod
from typing import Any


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

    def aggregate(
        self, table: str, func: str, column: "str | None" = None, group_by: "list | None" = None
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
        sql = f"SELECT {select} FROM {self._qualified(table)}"
        if groups:
            sql += " GROUP BY " + ", ".join(groups)
        return self._bounded(sql)

    def orphans(
        self, table: str, column: str, parent_table: str, parent_column: str, samples: int = 5
    ) -> tuple:
        """(orphan_count, sample orphan values): non-null child values with no parent."""
        from libs.engine.schema import ident

        c, p = ident(column, self.dialect), ident(parent_column, self.dialect)
        where = (
            f"FROM {self._qualified(table)} ch WHERE ch.{c} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {self._qualified(parent_table)} pa WHERE pa.{p} = ch.{c})"
        )
        count = int(self._bounded(f"SELECT COUNT(*) {where}")[0][0])
        n = int(samples)
        sample = [r[0] for r in self._bounded(f"SELECT ch.{c} {where} ORDER BY 1 LIMIT {n}")]
        return count, sample
