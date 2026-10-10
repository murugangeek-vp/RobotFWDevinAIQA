from abc import ABC, abstractmethod
from typing import Any


class SourceAdapter(ABC):
    """Stable interface for source systems (CSV today; new sources register
    via libs.adapters.registry without touching engine or suites)."""

    @abstractmethod
    def read_batch(self, **kwargs):
        """Return a pandas DataFrame of source rows."""

    def read_chunks(self, size: int = 50000):
        """Yield DataFrames of at most `size` rows. Default wraps read_batch;
        adapters with server-side cursors override this to stream big tables."""
        df = self.read_batch()
        for i in range(0, len(df), size):
            yield df.iloc[i : i + size]

    @abstractmethod
    def row_count(self) -> int:
        """Number of source records."""

    def post_snapshot_count(self) -> "int | None":
        """Rows inside the migration scope that arrived after the bound
        snapshot watermark. None when the source has no watermark predicate
        or the session is pinned to a snapshot that hides post-cut arrivals."""
        return None

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

    def read_chunks(
        self,
        table: str,
        columns: "list | None" = None,
        keys: "list | None" = None,
        size: int = 50000,
    ):
        """Yield DataFrames of at most `size` target rows, ordered by `keys`
        so callers can merge-compare streams. Default wraps read_table;
        adapters with server-side cursors override this to stream."""
        df = self.read_table(table, columns=columns)
        for i in range(0, len(df), size):
            yield df.iloc[i : i + size]

    @abstractmethod
    def query(self, sql: str, params: tuple = ()):
        """Run a read-only SELECT and return a pandas DataFrame — for dynamic
        rule-driven fetches (Excel WHERE clauses, per-rule column sets)."""

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
