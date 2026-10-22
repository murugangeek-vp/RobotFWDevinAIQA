"""Build the Phase-1 complex business-rules workbook:
data/rules/phase1_complex_rules.xlsx

Exercises the excelrules engine's complex-check surface against the hop-1
landed data (signature schema) and its read-only join view
v_dim_customer_src (CUP00301 LEFT JOIN CUP01201 LEFT JOIN DIM_REFERENCE):

- aggregation on the final table: GROUP BY/HAVING and correlated COUNT(*)
  inner selects as CUSTOM_SQL predicates
- inner select queries: IN-subquery, correlated EXISTS, and Where-Clause
  fragments containing subqueries
- multi-join logic: comma-list sources auto-join on the shared key; the
  file@acc_col=file_col form joins on differently-named keys; source-side
  joins are recomputed and compared against the DB-side join view
- complex business logic: CUSTOM_PYTHON conditional defaults, conditional
  SQL predicates, and cross-file extract-cut consistency

Run: .venv/Scripts/python.exe scripts/generate_phase1_rules.py
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "rules"

RULES_HEADERS = [
    "Test ID",
    "Rule Type",
    "Source Table",
    "Source Column(s)",
    "Target Table",
    "Target Column",
    "Transformation Logic",
    "Expected",
    "Severity",
    "Where Clause",
    "Key Column",
    "Enabled",
]

RULES = [
    [
        "BR-AGG-UNIQ-CUNBR",
        "CUSTOM_SQL",
        "",
        "",
        "cup00301",
        "cunbr",
        "cunbr IN (SELECT cunbr FROM signature.cup00301 " "GROUP BY cunbr HAVING COUNT(*) = 1)",
        "",
        "CRITICAL",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-AGG-BANK-CAP",
        "CUSTOM_SQL",
        "",
        "",
        "cup00301",
        "cubk",
        "(SELECT COUNT(*) FROM signature.cup00301 x WHERE x.cubk = cup00301.cubk) <= 80",
        "",
        "HIGH",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-FK-CUNBR-EXISTS",
        "CUSTOM_SQL",
        "",
        "",
        "cup01201",
        "cunbr",
        "EXISTS (SELECT 1 FROM signature.cup00301 p WHERE p.cunbr = cup01201.cunbr)",
        "",
        "CRITICAL",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-TAXID-FORMAT",
        "CUSTOM_SQL",
        "",
        "",
        "cup00301",
        "cussnr",
        "cussty IS NULL OR cussty <> 'S' OR cussnr IS NULL OR cussnr ~ '^[0-9]{9}$'",
        "",
        "HIGH",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-JOIN-LEGAL-ENTITY",
        "DIRECT_COMPARE",
        "cup00301, cup01201",
        "culegenty",
        "v_dim_customer_src",
        "culegenty",
        "",
        "",
        "HIGH",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-LOOKUP-TAXID-DESC",
        "LOOKUP",
        "cup00301, dim_reference@cussty=attribute_cd",
        "",
        "v_dim_customer_src",
        "attribute_value_desc",
        "lookup(attribute_value_desc)",
        "",
        "HIGH",
        "cussty IS NOT NULL",
        "cunbr",
        "yes",
    ],
    [
        "BR-DEFAULT-TAXID-DESC",
        "CUSTOM_PYTHON",
        "cup00301, cup01201, dim_reference@cussty=attribute_cd",
        "",
        "v_dim_customer_src",
        "attribute_value_desc",
        "attribute_value_desc if attribute_value_desc is not None " "else 'SOCIAL SECURITY NUMBER'",
        "",
        "HIGH",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-EXTRACT-CUT-DATE",
        "CUSTOM_PYTHON",
        "cup00301, cup01201",
        "load_date",
        "cup01201",
        "load_date",
        "load_date",
        "",
        "MEDIUM",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-STATE-FORMAT",
        "REGEX",
        "",
        "",
        "cup00301",
        "cuadst",
        "",
        "^[A-Z]{2}$",
        "MEDIUM",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-JOBDATE-FORMAT",
        "DATE_FORMAT",
        "",
        "",
        "cup00301",
        "cujbdt",
        "",
        "%Y%m%d",
        "MEDIUM",
        "",
        "cunbr",
        "yes",
    ],
    [
        "BR-SCOPED-NOTNULL",
        "NULL_CHECK",
        "",
        "",
        "cup01201",
        "culegenty",
        "",
        "",
        "HIGH",
        "EXISTS (SELECT 1 FROM signature.cup00301 c WHERE c.cunbr = cup01201.cunbr)",
        "cunbr",
        "yes",
    ],
]

README = [
    ["Phase-1 complex business rules — hop-1 landing (signature schema)."],
    [""],
    ["Run via tests/migration/10_business_rules.robot:"],
    ["  robot -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml"],
    ["        -v RULES_WORKBOOK:data/rules/phase1_complex_rules.xlsx"],
    ["        tests/migration/10_business_rules.robot"],
    [""],
    ["Rule coverage:"],
    ["  BR-AGG-*        aggregation on the final table via inner select"],
    ["  BR-FK-*/BR-SCOPED-*  correlated EXISTS / Where-Clause subqueries"],
    ["  BR-TAXID-*      conditional business predicate (S-type tax ids are 9 digits)"],
    ["  BR-JOIN-*       multi-file auto-join recomputed vs the DB join view"],
    ["  BR-LOOKUP-*/BR-DEFAULT-*  differing-key join + null-default logic"],
    ["  BR-EXTRACT-*    cross-file watermark consistency"],
]


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for r in README:
        ws.append(r)
    ws.column_dimensions["A"].width = 90
    rs = wb.create_sheet("Rules")
    rs.append(RULES_HEADERS)
    for c in rs[1]:
        c.font = Font(bold=True)
    for r in RULES:
        rs.append(r)
    out = OUT_DIR / "phase1_complex_rules.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"wrote {out} ({len(RULES)} rules)")


if __name__ == "__main__":
    main()
