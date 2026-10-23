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
    # key -> {"source": row|None, "target": row|None} — the full problem
    # record(s) behind a mismatch, bounded like the other samples.
    record_samples: dict = field(default_factory=dict)

    totals: dict = field(default_factory=dict)

    @property
    def mismatch_count(self) -> int:
        return sum(self.count(kind) for kind in ("missing_in_target", "extra_in_target", "diffs"))

    def count(self, kind: str) -> int:
        return self.totals.get(kind, len(getattr(self, kind)))

    def record(self, kind: str, value, sample_limit: int):
        self.totals[kind] = self.count(kind) + 1
        samples = getattr(self, kind)
        if len(samples) < sample_limit:
            samples.append(value)

    def record_sample(self, key, source_row, target_row, sample_limit: int):
        if len(self.record_samples) < sample_limit and key not in self.record_samples:
            self.record_samples[key] = {"source": source_row, "target": target_row}
