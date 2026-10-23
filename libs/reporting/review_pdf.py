"""Review-ready PDF report — the plain-English layer of the migration report.

One summary page (what was run, result, follow-up actions) followed by the
test-case evidence a reviewer needs: one section per executed Robot test
showing purpose, result, and recorded check detail in business language —
record counts, missing/unexpected records, changed fields. Technical internals
(SQL, catalog metadata, queries) stay in migration_summary.xlsx.
"""

import contextlib
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

REPORT_NAME = "end_to_end_report.pdf"

_STATUS_FILL = {
    "PASS": colors.HexColor("#C6EFCE"),
    "FAIL": colors.HexColor("#FFC7CE"),
    "ERROR": colors.HexColor("#FFC7CE"),
    "INCOMPLETE": colors.HexColor("#DDEBF7"),
    "REVIEW": colors.HexColor("#FFEB9C"),
    "SKIP": colors.HexColor("#FFEB9C"),
    "NOT RUN": colors.HexColor("#DDEBF7"),
}

_STAGES = {
    "mapping": ("Workbook mapping", "compiles with no spec errors"),
    "source": ("Source profile", "matches the workbook"),
    "dq": ("Source data quality", "has no failing rule violations"),
    "schema": ("Target schema", "matches the contract"),
    "recon": ("Record counts and values", "match the target exactly"),
    "transform": ("Value conversions", "recompute correctly"),
    "controls": ("Bank trailer controls", "all certified totals match"),
    "relations": ("Foreign keys", "no orphaned references"),
    "profile": ("Column profiles", "statistically match the source"),
    "negative": ("Defect injection self-test", "injected defects detected"),
}

_CASE_PURPOSE = {
    "mapping": "Confirms every enabled mapping in the workbook compiles",
    "source": "Profiles the source extract",
    "dq": "Applies workbook data-quality rules to source rows",
    "schema": "Compares the target schema to the contract",
    "recon": "Matches every record and value against the target",
    "transform": "Recomputes each declared transformation",
    "controls": "Reconciles declared totals to the bank trailer",
    "relations": "Checks foreign-key relations on both sides",
    "profile": "Compares column statistics on both sides",
    "negative": "Proves a tampered copy is caught",
    "e2e": "Runs the full evidence chain",
}

_SUMMARY = {
    "PASS": "all migration checks passed and the target matches the source.",
    "FAIL": "one or more migration checks found real data differences.",
    "INCOMPLETE": "not all planned checks ran or produced a result.",
    "REVIEW": "checks passed but some results need human review.",
    "NOT RUN": "the migration checks never ran.",
}

_ACTIONS = {
    "FAIL": "Review the failing evidence below, then fix data or mapping and re-run.",
    "INCOMPLETE": "Find the missing checks in the QA report and re-run before sign-off.",
    "REVIEW": "A reviewer should confirm the flagged results before sign-off.",
    "NOT RUN": "Re-run the validation suite; evidence is absent.",
}

# A technical stage detail is only reviewer-friendly when it reads like a
# count or status statement; anything else (SQL, paths, reprs) stays in the
# QA workbook.
def _reader_detail(row: dict) -> str:
    detail = row.get("detail") or ""
    for marker in ("missing", "extra", "diff", "orphan", "violat", "error", "expect"):
        if marker in detail.lower():
            return detail
    return ""


def _case_id(i: int) -> str:
    return f"TC-{i:02d}"


def _case_name(outcome: dict) -> str:
    """Robot longname `...Suite.Test Name` -> `Test Name`."""
    return str(outcome.get("test", "")).rsplit(".", 1)[-1]


def _stage_label(stage: str) -> str:
    return _STAGES.get(stage, (stage.replace("_", " ").title(), ""))[0]


def _stage_expected(stage: str) -> str:
    return _STAGES.get(stage, ("", "produces its expected result"))[1]


def _status(summary: dict, outcomes: list, rule_results) -> str:
    """Overall status — stricter than the engine verdict, never weaker."""
    verdict = summary.get("verdict", "NOT_RUN")
    if verdict == "NOT_RUN":
        return "NOT RUN"
    if outcomes and any(o.get("status") not in ("PASS",) for o in outcomes):
        if any(o.get("status") == "SKIP" for o in outcomes):
            return "INCOMPLETE"
    if outcomes and any(o.get("status") == "FAIL" for o in outcomes):
        return "FAIL"
    bad = [r.status for r in (rule_results or []) if r.status in ("FAIL", "ERROR")]
    if verdict == "FAIL" or bad:
        return "FAIL"
    warn = verdict == "PASS" and any(r.status == "WARN" for r in (rule_results or []))
    if warn:
        return "REVIEW"
    if summary.get("coverage") != "COMPLETE":
        return "INCOMPLETE"
    return "PASS"


def build_review(
    summary: dict,
    rule_results=None,
    declared_rules=None,
    test_outcomes=None,
) -> dict:
    """Review model from run_summary + rule results — plain-English only."""
    results = summary.get("results", [])
    outcomes = list(test_outcomes or [])
    status = _status(summary, outcomes, rule_results)

    executed = [o for o in outcomes if o.get("status") != "SKIP"]
    skipped = [o for o in outcomes if o.get("status") == "SKIP"]

    cases = []
    for i, outcome in enumerate(executed, 1):
        checks = outcome.get("checks", [])
        stages = sorted({c["stage"] for c in checks})
        purpose = next(
            (_CASE_PURPOSE[s] for s in stages if s in _CASE_PURPOSE),
            "Runs the declared migration checks",
        )
        if "e2e" in stages:
            expected = "All declared stage checks produce their expected results"
        else:
            expected = "; ".join(
                f"{_stage_label(s)}: {_stage_expected(s)}" for s in stages[:4]
            ) or "Recorded checks produce their expected results"
        failed = [c for c in checks if c["status"] != "PASS"]
        evidence = (
            f"{len(checks)} recorded check(s)"
            + (f"; {len(failed)} not passed" if failed else "")
        )
        if outcome.get("status") == "FAIL" and failed:
            first = _reader_detail(failed[0])
            if first:
                evidence += f" - {first[:160]}"
        cases.append(
            {
                "case_id": _case_id(i),
                "name": _case_name(outcome),
                "purpose": purpose,
                "expected": expected,
                "result": outcome.get("status", ""),
                "evidence": evidence,
            }
        )

    req_rows = [
        {"id": r.rule.test_id, "text": r.rule.rule_type.replace("_", " ").title()}
        for r in (rule_results or [])
        if getattr(r.rule, "severity", "").upper() in ("HIGH", "MEDIUM", "CRITICAL")
    ]

    return {
        "status": status,
        "summary_line": f"{status}: {_SUMMARY.get(status, '')}",
        "action": _ACTIONS.get(status),
        "meta": summary.get("meta", {}),
        "coverage": summary.get("coverage", ""),
        "cases": cases,
        "skipped": [_case_name(o) for o in skipped],
        "rule_rows": [
            {
                "test_id": r.rule.test_id,
                "rule_type": r.rule.rule_type.replace("_", " ").title(),
                "status": r.status,
                "violations": len(r.violations),
                "error": r.error or "",
            }
            for r in (rule_results or [])
        ],
        "requirements": req_rows,
        "signoff": "Production sign-off is NOT granted by this run",
    }


def write_review_pdf(
    out_dir,
    summary: dict,
    rule_results=None,
    declared_rules=None,
    test_outcomes=None,
) -> Path:
    """Render the reviewer report next to the other run artifacts."""
    review = build_review(summary, rule_results, declared_rules, test_outcomes)
    out = Path(out_dir) / REPORT_NAME
    status = review["status"]

    title = ParagraphStyle("title", fontSize=16, leading=20, spaceAfter=2 * mm)
    h = ParagraphStyle("h", fontSize=11, leading=14, spaceBefore=5 * mm, spaceAfter=2 * mm)
    body = ParagraphStyle("body", fontSize=9, leading=12)
    small = ParagraphStyle("small", fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
    right = ParagraphStyle("right", fontSize=9, leading=12, alignment=TA_RIGHT)

    def esc(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def P(text, style=body):
        return Paragraph(esc(text), style)

    def PM(text, style=body):
        return Paragraph(text, style)

    story = [
        Paragraph("Migration Validation Review", title),
        Paragraph("Plain-English summary for QA, managers, support and client reviewers", small),
        Spacer(1, 3 * mm),
        HRFlowable(color=colors.HexColor("#1F4E79"), thickness=1),
        Spacer(1, 4 * mm),
    ]

    banner = Table(
        [[P(status), P(review["summary_line"], right)]],
        colWidths=[30 * mm, 140 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), _STATUS_FILL.get(status, colors.white)),
                ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F5F5F5")),
                ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )
    story.append(banner)

    meta = review["meta"]
    story += [
        Paragraph("What was validated", h),
        PM(
            f"Environment: <b>{esc(meta.get('environment', ''))}</b> | "
            f"Hop: {esc(meta.get('hop', ''))} | "
            f"Datasets checked: {len(meta.get('selected') or [])} | "
            f"Ran: {esc(meta.get('timestamp') or meta.get('started', ''))} | "
            f"Snapshot/cut: {esc(meta.get('snapshot_id', ''))}"
        ),
        P(f"Coverage: {review['coverage']} | {review['signoff']}.", small),
    ]
    if review["action"]:
        story += [
            Spacer(1, 2 * mm),
            PM(f"<b>Follow-up:</b> {esc(review['action'])}"),
        ]
    if review["skipped"]:
        story.append(
            P(
                "Did not run: " + ", ".join(review["skipped"]) + " - see the QA report for why.",
                small,
            )
        )

    story += [Paragraph("Test cases and evidence", h)]

    header_style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
    )

    def table(header, rows, widths):
        t = Table([header] + rows, colWidths=widths, repeatRows=1)
        t.setStyle(header_style)
        return t

    if review["cases"]:
        rows = [
            [
                c["case_id"],
                P(c["name"], small),
                P(c["result"], small),
                P(c["expected"], small),
            ]
            for c in review["cases"]
        ]
        case_table = table(
            ["ID", "Test case", "Result", "What it checked"],
            rows,
            [14 * mm, 62 * mm, 20 * mm, 74 * mm],
        )
        for r, c in enumerate(review["cases"], 1):
            with contextlib.suppress(KeyError):
                case_table.setStyle(
                    TableStyle([("BACKGROUND", (2, r), (2, r), _STATUS_FILL[c["result"]])])
                )
        story.append(case_table)
    else:
        story.append(P("No test results were recorded.", small))

    if review["rule_rows"]:
        story += [Spacer(1, 2 * mm), Paragraph("Business rules", h)]
        rows = [
            [
                P(rr["test_id"], small),
                P(rr["rule_type"], small),
                P(rr["status"], small),
                P(rr["violations"] if not rr["error"] else f"error: {rr['error'][:60]}", small),
            ]
            for rr in review["rule_rows"]
        ]
        rule_table = table(
            ["Test", "Rule", "Result", "Violations"],
            rows,
            [40 * mm, 50 * mm, 20 * mm, 60 * mm],
        )
        for r, rr in enumerate(review["rule_rows"], 1):
            with contextlib.suppress(KeyError):
                rule_table.setStyle(
                    TableStyle([("BACKGROUND", (2, r), (2, r), _STATUS_FILL[rr["status"]])])
                )
        story.append(rule_table)
    elif review["requirements"]:
        story.append(
            P(f"{len(review['requirements'])} workbook rule(s) declared — none ran this run.", small)
        )

    if review["cases"]:
        story += [Spacer(1, 3 * mm), Paragraph("Test-case evidence and expected outcomes", h)]
        for c in review["cases"]:
            story.append(
                KeepTogether(
                    [
                        PM(f"<b>{esc(c['case_id'])} {esc(c['name'])}</b> - {esc(c['result'])}"),
                        P(f"Purpose: {c['purpose']}. Expected: {c['expected']}.", small),
                        P(f"Evidence: {c['evidence']}", small),
                        Spacer(1, 1.5 * mm),
                    ]
                )
            )

    story += [
        Spacer(1, 4 * mm),
        HRFlowable(color=colors.HexColor("#BBBBBB"), thickness=0.5),
        Paragraph("How to review", h),
        P(
            "PASS means the checks listed above ran and found no difference. FAIL lists the "
            "differences found - exact counts are in the QA report. INCOMPLETE or REVIEW needs a "
            "QA look before any sign-off. This PDF contains no customer values and no SQL; row-level "
            "detail lives in migration_summary.xlsx and the Robot log."
        ),
        P(
            f"Executed by: {meta.get('executed_by') or meta.get('executor', '')} | "
            f"Workbook: {meta.get('workbook', '')} | "
            f"Workbook SHA-256: {str(meta.get('workbook_sha256', ''))[:16]}... | "
            f"Commit: {meta.get('git_commit', '')}",
            small,
        ),
    ]

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Migration Validation Review",
        author="migration verification framework",
    )
    doc.build(story)
    return out
