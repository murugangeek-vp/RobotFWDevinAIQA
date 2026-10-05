"""Dataclasses for the Excel-driven rule layer."""

from dataclasses import dataclass, field

RULE_TYPES = (
    "MAX_LENGTH",
    "MIN_LENGTH",
    "UPPERCASE",
    "LOWERCASE",
    "TRIM",
    "CONCAT",
    "MAP",
    "DIRECT_COMPARE",
    "NULL_CHECK",
    "DATE_FORMAT",
    "DATE_TRANSFORM",
    "NUMERIC_ROUND",
    "DEFAULT_VALUE",
    "LOOKUP",
    "REGEX",
    "MASK",
    "HASH",
    "ENCRYPTION",
    "DECRYPTION",
    "SUBSTRING",
    "PREFIX",
    "SUFFIX",
    "CASE_WHEN",
    "CUSTOM_SQL",
    "CUSTOM_PYTHON",
)

# Severity -> whether violations fail the run (LOW/WARN are report-only).
FAILING_SEVERITIES = ("HIGH", "MEDIUM", "CRITICAL")


@dataclass
class Rule:
    """One row of the Rules sheet — the contract between Excel and engine."""

    test_id: str
    rule_type: str  # one of RULE_TYPES
    target_table: str
    target_column: str
    source_tables: list[str] = field(default_factory=list)  # csv stems, joinable
    source_columns: list[str] = field(default_factory=list)
    logic: str = ""  # transform expr e.g. "{branch_code}|{acct_seq}", "map(st, statuses)"
    expected: str | None = None  # e.g. "67" for MAX_LENGTH
    severity: str = "HIGH"
    where: str | None = None  # optional SQL WHERE fragment on the target fetch
    key_column: str | None = None  # "src=target" when names differ, else shared name
    enabled: bool = True

    def key_pair(self) -> "tuple[str | None, str | None]":
        """key_column -> (source_key, target_key); 'a=b' splits, plain = same."""
        if not self.key_column:
            return None, None
        if "=" in self.key_column:
            s, t = self.key_column.split("=", 1)
            return s.strip(), t.strip()
        return self.key_column, self.key_column


@dataclass
class Violation:
    """One row-level breach — enough detail to locate it without a debugger."""

    test_id: str
    column: str
    expected: object
    actual: object
    key: object = None

    def as_dict(self) -> dict:
        d = {
            "test_id": self.test_id,
            "column": self.column,
            "expected": self.expected,
            "actual": self.actual,
        }
        if self.key is not None:
            d["key"] = self.key
        return d


@dataclass
class RuleResult:
    rule: Rule
    violations: list[Violation] = field(default_factory=list)
    error: "str | None" = None  # rule couldn't evaluate (missing column, bad expr)
    sql: "str | None" = None  # target SELECT issued for this rule (audit trail)

    @property
    def status(self) -> str:
        if self.error:
            return "ERROR"
        if not self.violations:
            return "PASS"
        return "FAIL" if self.rule.severity.upper() in FAILING_SEVERITIES else "WARN"
