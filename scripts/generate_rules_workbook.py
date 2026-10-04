"""Build data/rules/migration_rules.xlsx — the sample workbook that proves
the Excel-driven layer. Deterministic output so the artifact is auditable and
regenerable; the committed .xlsx is what the tests read.

Run: .venv/Scripts/python.exe scripts/generate_rules_workbook.py
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "rules" / "migration_rules.xlsx"

HEADERS = [
    "Test ID", "Source Table", "Source Column(s)", "Target Table",
    "Target Column", "Rule Type", "Transformation Logic", "Expected",
    "Severity", "Where Clause", "Key Column", "Enabled",
]

# Mirrors the contract-derived rules so both layers check the same truth.
# ER-105 exercises a join across two source files; ER-107 is deliberately
# disabled (proves Enabled=no is skipped); ER-108 is LOW (report-only).
RULES = [
    ["ER-001", "", "", "address_pilot", "address2", "MAX_LENGTH", "", "67",
     "HIGH", "", "address_id", "yes"],
    ["ER-002", "address_extract", "address2", "address_pilot", "address2",
     "NULL_CHECK", "", "", "HIGH", "", "address_id", "yes"],
    ["ER-003", "customer", "country", "customer", "country", "UPPERCASE",
     "upper({country})", "", "HIGH", "", "customer_id", "yes"],
    ["ER-004", "customer", "email", "customer", "email", "LOWERCASE",
     "lower({email})", "", "HIGH", "", "customer_id", "yes"],
    ["ER-005", "account_extract,account_codes", "branch_code,acct_seq",
     "account_loan", "loan_account", "CONCAT", "{branch_code}|{acct_seq}", "",
     "HIGH", "", "acct_seq=account_seq", "yes"],
    ["ER-006", "account_extract", "prod_code", "account_loan", "product_name",
     "MAP", "map(prod_code, products)", "", "HIGH", "", "acct_seq=account_seq",
     "yes"],
    ["ER-007", "customer", "status", "customer", "status",
     "DIRECT_COMPARE", "", "", "MEDIUM", "", "customer_id", "yes"],
    ["ER-008", "customer", "balance", "customer", "balance", "NULL_CHECK", "",
     "", "LOW", "status = 'active'", "customer_id", "no"],
    ["ER-009", "address_extract", "address1", "address_pilot", "address1",
     "MAX_LENGTH", "", "100", "LOW", "", "address_id", "yes"],
]

MAPPINGS = [
    ("products", "LN", "LOAN"),
    ("products", "FD", "FIXED_DEPOSIT"),
    ("products", "RD", "RECURRING_DEPOSIT"),
    ("statuses", "A", "ACTIVE"),
    ("statuses", "C", "CLOSED"),
    ("statuses", "S", "SUSPENDED"),
]


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "Rules"
    ws.append(HEADERS)
    for row in RULES:
        ws.append(row)
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, width in zip("ABCDEFGHIJKL", [9, 34, 22, 16, 15, 15, 28, 9, 9, 18, 20, 8]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    ms = wb.create_sheet("Mapping")
    ms.append(["Mapping Name", "Source Value", "Target Value"])
    for c in ms[1]:
        c.font = Font(bold=True)
    for row in MAPPINGS:
        ms.append(list(row))
    for col, width in zip("ABC", [16, 14, 18]):
        ms.column_dimensions[col].width = width

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT} ({len(RULES)} rules, {len(MAPPINGS)} mappings)")


if __name__ == "__main__":
    main()
