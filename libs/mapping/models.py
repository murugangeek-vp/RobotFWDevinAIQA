from dataclasses import dataclass, field

CONTRACT_TYPES = {"integer", "decimal", "string", "date", "timestamp", "boolean"}
RECON_MODES = {"full", "hash"}
CONTROL_TYPES = {"count", "sum", "min", "max", "null_count", "count_distinct"}


@dataclass
class FieldMapping:
    """One FieldMap row: how a source column reaches a target column."""

    mapping_id: str
    source_column: str
    target_column: str
    target_type: str
    nullable: bool = True
    max_length: "int | None" = None
    scale: "int | None" = None
    transformation: "str | None" = None
    enabled: bool = True


@dataclass
class TableMapping:
    """One Tables row + its FieldMap rows — one source->target table pair."""

    mapping_id: str
    source_schema: str
    source_table: str
    target_schema: str
    target_table: str
    keys: list
    where: "str | None" = None
    enabled: bool = True
    recon_mode: str = "full"
    batch_size: "int | None" = None
    fields: list = field(default_factory=list)  # list[FieldMapping]
    controls: list = field(default_factory=list)  # Controls-sheet aggregate specs
