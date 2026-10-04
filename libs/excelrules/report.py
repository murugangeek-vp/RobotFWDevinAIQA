"""Write the Excel summary report: one row per rule with status, violation
count, and a diff sample — the audit artifact a reviewer opens next to the
Robot HTML reports."""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import RuleResult

_FILL = {"PASS": "C6EFCE", "WARN": "FFEB9C", "FAIL": "FFC7CE", "ERROR": "FFC7CE"}


def write_summary(results: list[RuleResult], out_path: "str | Path") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Test ID", "Rule Type", "Target", "Severity", "Status",
               "Violations", "Sample", "Error"])
    for c in ws[1]:
        c.font = Font(bold=True)

    for r in results:
        rule = r.rule
        sample = "; ".join(
            f"{v.column} key={v.key}: expected {v.expected!r} actual {v.actual!r}"
            for v in r.violations[:5]
        )
        ws.append([rule.test_id, rule.rule_type, f"{rule.target_table}.{rule.target_column}",
                   rule.severity, r.status, len(r.violations), sample, r.error or ""])
        status_cell = ws.cell(row=ws.max_row, column=5)
        status_cell.fill = PatternFill("solid", fgColor=_FILL.get(r.status, "FFFFFF"))

    for i, width in enumerate([10, 16, 34, 10, 8, 11, 80, 40], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
