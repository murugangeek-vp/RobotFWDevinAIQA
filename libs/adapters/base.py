from abc import ABC, abstractmethod
from typing import Any


class SourceAdapter(ABC):
    """Stable interface for source systems (CSV today; new sources register
    via libs.adapters.registry without touching engine or suites)."""

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
