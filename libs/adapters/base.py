from abc import ABC, abstractmethod


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
    """Stable interface for targets. Verification paths must be read-only."""

    @abstractmethod
    def read_table(self, table: str, columns: list = None):
        """Return a pandas DataFrame of target rows."""

    @abstractmethod
    def row_count(self, table: str) -> int:
        """Number of records in the target table."""

    @abstractmethod
    def schema(self, table: str) -> list:
        """[(column_name, data_type, is_nullable), ...]"""

    @abstractmethod
    def close(self):
        """Release resources."""
