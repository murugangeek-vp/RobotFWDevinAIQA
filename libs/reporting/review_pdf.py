"""Plain-English review PDF from current-run evidence; no queries or customer values."""

from collections import Counter
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_CASES = [
    (
        "Requirements Intake And Mapping Compile",
        "TC-01",
        "Check the migration instructions",
        "The selected tables and column mappings must be valid.",
        ("mapping",),
    ),
    (
        "Source And Schema Conformance",
        "TC-02",
        "Check that data is ready",
        "Source data must meet its quality rules and the destination structure must match.",
        ("source", "dq", "schema"),
    ),
    (
        "Transformation And Record Reconciliation",
        "TC-03",
        "Check transferred records and values",
        "Every in-scope record must arrive, with the expected values after conversion.",
        ("recon", "transform"),
    ),
    (
        "Business Validation Controls Relations Profile And Rules",
        "TC-04",
        "Check business requirements",
        "Certified totals, links between records, column statistics and declared business rules must agree.",
        ("controls", "relations", "profile"),
    ),
    (
        "Lifecycle Evidence Is Complete And Passing",
        "TC-05",
        "Check that evidence is complete",
        "All required checks must finish successfully and the required evidence must be available.",
        (),
    ),
]
_STAGES = {
    "mapping": "Migration instructions",
    "source": "Source data available",
    "dq": "Source data quality",
    "schema": "Destination structure",
    "recon": "Records and values",
    "transform": "Value conversions",
    "controls": "Certified totals",
    "relations": "Links between records",
    "profile": "Column statistics",
}
_ACTIONS = {
    "TC-01": "QA and the mapping owner: correct the workbook, then rerun the checks.",
    "TC-02": "QA and the data owner: review source quality and destination structure.",
    "TC-03": "Migration team: investigate missing, unexpected or changed values; correct and rerun.",
    "TC-04": "Business/data owner: investigate totals, record links and business-rule results.",
    "TC-05": "QA: complete the missing checks and review the final evidence before approval.",
}


def _status(values) -> str:
    """Never turn skipped, missing or unrecognised evidence into a pass."""
    values = list(values)
    if any(v in ("FAIL", "ERROR") for v in values):
        return "FAIL"
    if not values or any(v not in ("PASS", "WARN") for v in values):
        return "INCOMPLETE"
    return "REVIEW" if "WARN" in values else "PASS"


def build_review(run, rule_results, trace=None, declared_rules=None, test_outcomes=None):
    """Build a small allowlisted view. Raw errors, SQL and compared values stay in QA evidence."""
    evidence = run.get("results") or []
    meta = run.get("meta") or {}
    mappings = meta.get("selected") or sorted(
        {r["mapping"] for r in evidence if r.get("mapping") and r.get("stage") != "setup"}
    )
    rules = declared_rules if declared_rules is not None else [r.rule for r in rule_results]
    results_by_id = {r.rule.test_id: r for r in rule_results}
    rule_rows = []
    for rule in rules:
        if not rule.enabled:
            continue
        result = results_by_id.get(rule.test_id)
        rule_rows.append(
            {
                "id": rule.test_id,
                "requirement": rule.requirement_id or "Not assigned",
                "description": rule.business_rule
                or f"Check {rule.target_table}.{rule.target_column}",
                "status": result.status if result else "NOT RUN",
                "evidence": (
                    "Check could not complete; QA must review the execution log."
                    if result and result.error
                    else f"{len(result.violations)} reported violation(s)."
                    if result
                    else "No execution evidence recorded."
                ),
            }
        )

    by_check = {}
    for row in evidence:
        by_check.setdefault((row.get("mapping"), row.get("stage")), []).append(row)
    outcomes = test_outcomes or []
    cases = []
    for name, case_id, title, purpose, stages in _CASES:
        checks = []
        missing = 0
        for mapping in mappings:
            for stage in stages:
                if stage == "relations" and mapping not in meta.get("relation_children", []):
                    continue
                rows = by_check.get((mapping, stage), [])
                checks.extend(r.get("status") for r in rows)
                if not rows:
                    missing += 1
                    checks.append("NOT RUN")
        if case_id == "TC-04":
            checks.extend(r["status"] for r in rule_rows)
            missing += sum(r["status"] == "NOT RUN" for r in rule_rows)
        if case_id == "TC-05":
            checks = [run.get("verdict")]
            if run.get("coverage") != "COMPLETE" or any((run.get("not_run") or {}).values()):
                checks.append("NOT RUN")
            checks.extend(r["status"] for r in rule_rows)
            checks.extend(r.get("status") for r in evidence)
            if (trace or {}).get("uncovered"):
                checks.append("NOT RUN")
        actual = [
            t.get("status")
            for t in outcomes
            if t.get("test") == name or t.get("test", "").endswith("." + name)
        ]
        # These are actual Robot test results, not inferred test passes.
        status = _status([*checks, *(actual or ["NOT RUN"])])
        if not mappings and status != "FAIL":
            status = "INCOMPLETE"
        cases.append(
            {
                "id": case_id,
                "name": title,
                "purpose": purpose,
                "status": status,
                "evidence": (
                    f"{len(checks) - missing} recorded check result(s); {missing} not recorded."
                    if stages
                    else f"Stage coverage: {run.get('coverage', 'NOT RECORDED')}."
                ),
            }
        )

    status = _status(
        [run.get("verdict"), *[c["status"] if c["status"] != "REVIEW" else "WARN" for c in cases]]
    )
    if any(r.get("status") in ("ERROR", "FAIL") for r in evidence):
        status = "FAIL"
    datasets = []
    for mapping in mappings:
        recon = by_check.get((mapping, "recon"), [])
        row = recon[-1] if recon else {}
        datasets.append(
            {
                "mapping": mapping,
                "source": row.get("source", ""),
                "target": row.get("target", ""),
                "counts": [
                    row.get(k, "Not recorded")
                    for k in ("source_count", "target_count", "missing", "extra", "diffs")
                ],
                "status": _status(r.get("status") for r in recon),
            }
        )
    # Each result is a check, not a distinct customer or record.
    checks = []
    for stage, label in _STAGES.items():
        if stage == "recon":
            continue
        rows = [r for r in evidence if r.get("stage") == stage]
        if rows:
            counts = Counter(r.get("status", "NOT RUN") for r in rows)
            proof = "; ".join(f"{n} {s}" for s, n in sorted(counts.items()))
            list_counts = {
                "mapping": ("field_mappings", "column mappings"),
                "schema": ("schema_errors", "structure issues"),
                "controls": ("control_mismatches", "total mismatches"),
                "profile": ("profile_diffs", "statistic differences"),
            }
            if stage in list_counts:
                key, description = list_counts[stage]
                if all(isinstance(r.get(key), list) for r in rows):
                    proof += f"; {sum(len(r[key]) for r in rows)} {description}"
            if stage == "dq" and all(isinstance(r.get("dq_failures"), dict) for r in rows):
                failures = [v for r in rows for v in r["dq_failures"].values()]
                if all(isinstance(v, int) for v in failures):
                    proof += f"; {sum(failures)} rule violations"
            checks.append([label, proof])
    return {
        "status": status,
        "cases": cases,
        "datasets": datasets,
        "rules": rule_rows,
        "checks": checks,
    }


def write_review_pdf(
    run, rule_results, out_dir, meta=None, trace=None, declared_rules=None, test_outcomes=None
) -> Path:
    """Write a self-contained review copy for the selected hop/run, without sensitive samples."""
    review = build_review(run, rule_results, trace, declared_rules, test_outcomes)
    audit = {**(run.get("meta") or {}), **(meta or {})}
    started = audit.get("started") or audit.get("timestamp") or "Not recorded"
    # Keep an unrecognised recorded timestamp visible.
    with suppress(ValueError):
        started = datetime.fromisoformat(started).strftime("%d %b %Y, %H:%M %z")
    step = str(audit.get("hop") or "Not recorded").replace("_", " ")
    if step.startswith("hop"):
        step = "Step " + step[3:]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "end_to_end_report.pdf"
    fonts = Path(reportlab.__file__).parent / "fonts"
    if "Review" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Review", str(fonts / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("ReviewBold", str(fonts / "VeraBd.ttf")))
    ink = colors.HexColor("#172D46")
    muted = colors.HexColor("#45566B")
    body = ParagraphStyle(
        "ReviewBody",
        fontName="Review",
        fontSize=9,
        leading=13,
        textColor=ink,
        spaceAfter=5,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle("ReviewSmall", parent=body, fontSize=8, leading=11)
    heading = ParagraphStyle(
        "ReviewHeading",
        parent=body,
        fontName="ReviewBold",
        fontSize=14,
        leading=18,
        spaceBefore=12,
        spaceAfter=8,
    )
    title = ParagraphStyle("ReviewTitle", parent=heading, fontSize=24, leading=29)
    header = ParagraphStyle(
        "ReviewHeader", parent=small, fontName="ReviewBold", textColor=colors.white
    )
    width = 174 * mm
    story = []

    def p(value, style=body):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    def table(headers, rows, widths):
        data = [[p(h, header) for h in headers]]
        data.extend([[p(c, small) for c in row] for row in rows])
        t = Table(
            data, colWidths=[width * w for w in widths], repeatRows=1, hAlign="LEFT", splitInRow=1
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), ink),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F0F4F8")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, 0), 1, ink),
                ]
            )
        )
        story.append(t)

    story.extend(
        [p("MIGRATION QUALITY REVIEW", small), p("End-to-End Migration\nValidation Report", title)]
    )
    verdict_text = {
        "PASS": "All five test cases passed for the selected scope.",
        "FAIL": "Issues were found. Correct the failed checks and rerun before approval.",
        "INCOMPLETE": "Evidence is incomplete. A successful result cannot be confirmed.",
        "REVIEW": "Checks completed with warnings. QA review is required.",
    }
    story.extend(
        [p(f"Overall result: {review['status']}", heading), p(verdict_text[review["status"]])]
    )
    table(
        ["Run", "Scope"],
        [
            ["Environment", audit.get("environment") or "Not recorded"],
            [
                "Migration step",
                f"{step}; {len(review['datasets'])} selected dataset(s)",
            ],
            ["Execution started", started],
            [
                "Data snapshot / cut",
                (run.get("cutover") or {}).get("snapshot_id") or "Not recorded",
            ],
        ],
        [0.28, 0.72],
    )
    story.append(p("Test cases", heading))
    table(
        ["Test case", "What was checked", "Result"],
        [[c["id"], c["name"], c["status"]] for c in review["cases"]],
        [0.14, 0.68, 0.18],
    )
    story.extend(
        [
            Spacer(1, 4 * mm),
            p(
                "PASS = check succeeded. FAIL = a failed check or execution error. "
                "INCOMPLETE = missing, skipped or unconfirmed checks. "
                "REVIEW = warnings require assessment.",
                small,
            ),
            p(
                "This report covers this migration step and the selected workbook mappings only. "
                "It is test evidence, not production approval or a regulatory certification.",
                small,
            ),
        ]
    )
    story.append(p("Report identity", heading))
    table(
        ["Audit field", "Value"],
        [
            ["Executed by", audit.get("executed_by") or audit.get("executor") or "Not recorded"],
            [
                "Workbook",
                Path(audit.get("mapping_workbook") or audit.get("rules_workbook") or "").name
                or "Not recorded",
            ],
            ["Workbook fingerprint (SHA-256)", audit.get("workbook_sha256") or "Not recorded"],
            ["Code version", audit.get("git_commit") or "Not recorded"],
        ],
        [0.29, 0.71],
    )
    story.extend(
        [
            PageBreak(),
            p("Evidence supporting the result", heading),
            p("Record comparison", heading),
            p(
                "Source and destination counts are per dataset. Missing means a source record "
                "did not arrive. Unexpected means a destination record has no matching source "
                "record. Changed values counts field differences, not distinct records.",
                small,
            ),
        ]
    )
    table(
        ["Dataset / mapping", "Source", "Destination", "Missing", "Unexpected", "Changed values"],
        [[d["mapping"], *d["counts"]] for d in review["datasets"]]
        or [["No comparison evidence", *["Not recorded"] * 5]],
        [0.30, 0.14, 0.14, 0.13, 0.14, 0.15],
    )
    story.append(p("Test-case evidence and expected outcome", heading))
    table(
        ["Case", "Expected outcome", "Recorded evidence"],
        [[c["id"], c["purpose"], c["evidence"]] for c in review["cases"]],
        [0.11, 0.53, 0.36],
    )
    story.append(p("Other validation checks", heading))
    table(
        ["Check", "Recorded results across selected datasets"],
        review["checks"] or [["No stage evidence", "INCOMPLETE"]],
        [0.42, 0.58],
    )
    if review["rules"]:
        story.extend(
            [
                PageBreak(),
                p("Business-rule test cases", heading),
                p(
                    "These workbook-defined checks run within TC-04. Counts below are reported "
                    "violations, not unique affected customers. A rule with no recorded result "
                    "is not treated as passed.",
                    small,
                ),
            ]
        )
        table(
            ["Test / requirement", "Business requirement", "Result and evidence"],
            [
                [
                    r["id"] + "\n" + r["requirement"],
                    r["description"],
                    r["status"] + "\n" + r["evidence"],
                ]
                for r in review["rules"]
            ],
            [0.28, 0.48, 0.24],
        )
    else:
        story.append(p("No additional business rules were declared in this workbook.", small))

    action_start = len(story)
    story.append(p("Actions and review", heading))
    actions = [[c["id"], _ACTIONS[c["id"]]] for c in review["cases"] if c["status"] != "PASS"]
    table(
        ["Reference", "Suggested next action"],
        actions
        or [
            [
                "All cases passed",
                "QA reviews the scope and evidence. The authorised business owner "
                "makes the release decision; this automated run does not grant approval.",
            ]
        ],
        [0.20, 0.80],
    )
    if trace is not None:
        reqs = {r["req"] for r in trace.get("rtm_rows", [])}
        uncovered = trace.get("uncovered") or []
        story.append(
            p(
                f"Requirement links: {len(reqs)} declared requirement ID(s); "
                f"{len(uncovered)} with missing check evidence. Coverage does not itself "
                "mean the checks passed.",
                small,
            )
        )
    story.append(p("Evidence reference", heading))
    story.append(
        p(
            "For QA follow-up, open end_to_end_summary.xlsx: Stages and Controls for execution "
            "results, Rules and Violations for business checks, and Data Diffs, Queries, Column Profile "
            "and Metadata for diagnosis. Match the mapping name and stage, or the business-rule ID. "
            "TC-01 to TC-05 follow the five lifecycle tests in execution order; "
            "output.xml and test_evidence_map.json contain their original test names and outcomes. "
            "The PDF intentionally excludes customer values, query text and raw error messages.",
            small,
        )
    )
    story[action_start:] = [KeepTogether(story[action_start:])]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Review", 8)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 12 * mm, "Migration validation | Review copy")
        canvas.drawRightString(192 * mm, 12 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path),
        pagesize=(210 * mm, 297 * mm),
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title="End-to-End Migration Validation Report",
        author="Migration QA",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
