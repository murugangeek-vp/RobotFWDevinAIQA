from dataclasses import dataclass, field
from typing import Any


@dataclass
class Contract:
    """Parsed data contract (config/contracts/*.yaml)."""
    raw: dict
    path: str

    @property
    def name(self) -> str:
        return self.raw["contract_name"]

    @property
    def version(self) -> str:
        return self.raw["version"]

    @property
    def source_columns(self) -> list:
        return self.raw["source"].get("columns", [])

    @property
    def columns(self) -> list:
        return self.raw["columns"]

    @property
    def keys(self) -> list:
        return self.raw["keys"]

    @property
    def table(self) -> str:
        return self.raw["target"]["table"]

    @property
    def schema(self) -> str:
        return self.raw["target"].get("schema", "public")

    @property
    def metadata(self) -> dict:
        return self.raw.get("metadata", {})

    def column(self, name: str) -> dict:
        for col in self.columns:
            if col["name"] == name:
                return col
        raise KeyError(f"column '{name}' not in contract")


@dataclass
class RuleFailure:
    rule_id: str
    column: str
    failing_rows: int
    samples: list = field(default_factory=list)


@dataclass
class ColumnDiff:
    key: Any
    column: str
    expected: Any
    actual: Any


@dataclass
class ReconResult:
    source_count: int = 0
    target_count: int = 0
    missing_in_target: list = field(default_factory=list)
    extra_in_target: list = field(default_factory=list)
    diffs: list = field(default_factory=list)  # list[ColumnDiff]

    @property
    def mismatch_count(self) -> int:
        return len(self.missing_in_target) + len(self.extra_in_target) + len(self.diffs)
