"""Migration report PDF — the readable version of migration_summary.xlsx.

Header block (verdict, environment, snapshot, audit identity), the executed
Robot test cases, then every recorded stage check per mapping with its
status and counts, then a Failures section with the reason and bounded
proof samples (missing/unexpected keys, value diffs, control mismatches,
DQ/schema errors, orphans) so a reviewer can see exactly what failed and
the evidence behind it — without opening the Excel workbook.
"""

import contextlib
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
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

REPORT_NAME = "migration_report.pdf"

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
    "e2e": ("All stage checks", "produce their expected results"),
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
    "FAIL": "Review the failures and proof below, then fix data or mapping and re-run.",
    "INCOMPLETE": "Find the missing checks in the QA report and re-run before sign-off.",
    "REVIEW": "A reviewer should confirm the flagged results before sign-off.",
    "NOT RUN": "Re-run the validation suite; evidence is absent.",
}

_PROOF_BOUND = 8  # samples per category per failure — bounded, like the xlsx


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
    if any(o.get("status") == "FAIL" for o in outcomes):
        return "FAIL"
    if any(o.get("status") == "SKIP" for o in outcomes):
        return "INCOMPLETE"
    bad = [r.status for r in (rule_results or []) if r.status in ("FAIL", "ERROR")]
    if verdict == "FAIL" or bad:
        return "FAIL"
    if any(r.status == "WARN" for r in (rule_results or [])):
        return "REVIEW"
    if summary.get("coverage") != "COMPLETE":
        return "INCOMPLETE"
    return "PASS"


def _proof_lines(row: dict) -> list:
    """Bounded, reviewer-readable proof for one failed stage row — the same
    categories the Diffs & Violations sheet records, capped at _PROOF_BOUND."""

    def cap(items):
        items = list(items)
        if len(items) > _PROOF_BOUND:
            items = items[:_PROOF_BOUND] + [f"+{len(items) - _PROOF_BOUND} more"]
        return items

    lines = []
    lines += cap(f"missing key {k} - absent in target" for k in row.get("missing_samples") or [])
    lines += cap(f"unexpected key {k} - absent in source" for k in row.get("extra_samples") or [])
    lines += cap(
        f"{d.get('column', '?')} key={d.get('key', '?')}: expected {d.get('expected', '?')}, "
        f"got {d.get('actual', '?')}"
        for d in row.get("diff_samples") or []
    )
    lines += cap(
        f"control {m.get('control', '?')}: expected {m.get('expected', '?')}, "
        f"got {m.get('actual', '?')}"
        for m in row.get("control_mismatches") or []
        if isinstance(m, dict)
    )
    for rid, n in (row.get("dq_failures") or {}).items():
        if isinstance(n, list):
            lines.append(f"DQ {rid}: samples {', '.join(str(x) for x in n[:_PROOF_BOUND])}")
        else:
            lines.append(f"DQ {rid}: {n} failing row(s)")
    lines += cap(f"schema: {e}" for e in row.get("schema_errors") or [])
    lines += cap(
        f"orphan key {o} - no matching parent record" for o in row.get("orphan_samples") or []
    )
    for key, label in (
        ("source_orphans", "source orphan key"),
        ("target_orphans", "target orphan key"),
    ):
        orphans = row.get(key)
        if isinstance(orphans, list):
            lines += cap(f"{label} {o}" for o in orphans)
    lines += cap(
        f"profile {d.get('column', '?')}.{d.get('stat', d.get('issue', '?'))}: "
        f"source {d.get('source', '?')} vs target {d.get('target', '?')}"
        for d in row.get("profile_diffs") or []
    )
    lines += cap(
        f"unmapped source table {t} - no workbook mapping"
        for t in row.get("unmapped_source_tables") or []
    )
    lines += cap(
        f"unmapped target table {t} - no workbook mapping"
        for t in row.get("unmapped_target_tables") or []
    )
    return lines


def _problem_columns(records: list, mapping_info: dict) -> list:
    """Columns worth showing in a problem-record table — the reconciliation
    key, foreign keys, date columns, and the columns that actually differ.
    Falls back to every column when nothing can be identified."""
    all_cols = []
    for _, sides in records:
        for rec in (sides.get("source"), sides.get("target")):
            if rec:
                all_cols += [c for c in rec if c not in all_cols]
    if not all_cols:
        return []

    fields = mapping_info.get("fields") or []
    keys = {str(k) for k in mapping_info.get("keys") or []}
    fks = {str(k) for k in mapping_info.get("fk_columns") or []}
    keep = set(keys | fks)
    for f in fields:
        if f.get("target") in keys | fks or f.get("type") == "date":
            keep.update((f.get("target"), f.get("source")))
    keep |= {c for c in all_cols if "date" in c.lower() or c.lower().endswith("dt")}
    for _, sides in records:
        src, tgt = sides.get("source"), sides.get("target")
        if src and tgt:
            keep |= {c for c in all_cols if src.get(c) != tgt.get(c)}
    cols = [c for c in all_cols if c in keep]
    return cols or all_cols


def build_report(summary: dict, rule_results=None, test_outcomes=None) -> dict:
    """Report model from run_summary + outcomes — every check, every failure."""
    results = summary.get("results", [])
    outcomes = list(test_outcomes or [])
    status = _status(summary, outcomes, rule_results)

    cases = []
    for i, outcome in enumerate((o for o in outcomes if o.get("status") != "SKIP"), start=1):
        checks = outcome.get("checks", [])
        stages = sorted({c["stage"] for c in checks})
        purpose = next(
            (_CASE_PURPOSE[s] for s in stages if s in _CASE_PURPOSE),
            "Runs the declared migration checks",
        )
        if "e2e" in stages:
            expected = "All declared stage checks produce their expected results"
        else:
            expected = (
                "; ".join(f"{_stage_label(s)}: {_stage_expected(s)}" for s in stages[:4])
                or "Recorded checks produce their expected results"
            )
        failed = [c for c in checks if c["status"] != "PASS"]
        if not checks:
            evidence = (
                "evidence-level check - see Robot log for detail"
                if outcome.get("status") == "FAIL"
                else "evidence-level check"
            )
        else:
            evidence = f"{len(checks)} recorded check(s)" + (
                f"; {len(failed)} not passed" if failed else ""
            )
        cases.append(
            {
                "case_id": f"TC-{i:02d}",
                "name": _case_name(outcome),
                "purpose": purpose,
                "expected": expected,
                "result": outcome.get("status", ""),
                "evidence": evidence,
            }
        )

    # The same check runs in several suites (per-stage + pipeline); keep one
    # row per mapping/stage/result so the report shows each check once.
    seen_rows = set()
    stage_rows = []
    for r in results:
        key = (r.get("mapping"), r.get("stage"), r.get("status"), r.get("detail"))
        if key in seen_rows:
            continue
        seen_rows.add(key)
        mismatch = (
            r.get("missing")
            or r.get("extra")
            or r.get("diffs")
            or r.get("mismatches")
            or len(r.get("control_mismatches") or [])
            or len(r.get("profile_diffs") or [])
            or len(r.get("schema_errors") or [])
            or len(r.get("dq_failures") or {})
            or len(r.get("orphan_samples") or [])
            or ""
        )
        stage_rows.append(
            {
                "mapping": r.get("mapping", ""),
                "stage": _stage_label(r.get("stage", "")),
                "status": r.get("status", ""),
                "src": r.get("source_count", ""),
                "tgt": r.get("target_count", ""),
                "mismatches": mismatch,
                "seconds": r.get("seconds", ""),
            }
        )

    mappings_by_id = {m["mapping_id"]: m for m in summary.get("meta", {}).get("mappings") or []}
    seen_failures = set()
    failures = []
    for r in results:
        key = (r.get("mapping"), r.get("stage"), r.get("status"), r.get("detail"))
        if r.get("status") == "PASS" or key in seen_failures:
            continue
        seen_failures.add(key)
        records = list((r.get("record_samples") or {}).items())
        failures.append(
            {
                "heading": f"{r.get('mapping', '')} / {_stage_label(r.get('stage', ''))} "
                f"- {r.get('status', '')}",
                "table": r.get("target") or r.get("source") or "",
                "reason": (r.get("detail") or "")[:400],
                "proof": _proof_lines(r),
                "records": records[:_PROOF_BOUND],
                "records_total": len(records),
                "record_columns": _problem_columns(
                    records, mappings_by_id.get(r.get("mapping"), {})
                ),
            }
        )

    seen_fc = set()
    file_comparison = []
    for r in results:
        if r.get("stage") != "negative":
            continue
        for fc in r.get("file_comparison") or []:
            k = (r.get("mapping"), fc.get("variant"))
            if k in seen_fc:
                continue
            seen_fc.add(k)
            file_comparison.append({"mapping": r.get("mapping", ""), **fc})

    seen_prof = set()
    profile_rows = []
    for r in results:
        for p in r.get("column_profile") or []:
            k = (r.get("mapping"), p.get("column"))
            if k in seen_prof:
                continue
            seen_prof.add(k)
            profile_rows.append(
                {
                    "mapping": r.get("mapping", ""),
                    "column": p.get("column", ""),
                    "src_nulls": p.get("src_nulls", ""),
                    "tgt_nulls": p.get("tgt_nulls", ""),
                    "src_distinct": p.get("src_distinct", ""),
                    "tgt_distinct": p.get("tgt_distinct", ""),
                    "differs": (
                        p.get("src_nulls") != p.get("tgt_nulls")
                        or p.get("src_distinct") != p.get("tgt_distinct")
                    ),
                }
            )

    return {
        "status": status,
        "summary_line": f"{status}: {_SUMMARY.get(status, '')}",
        "action": _ACTIONS.get(status),
        "meta": summary.get("meta", {}),
        "coverage": summary.get("coverage", ""),
        "cases": cases,
        "skipped": [_case_name(o) for o in outcomes if o.get("status") == "SKIP"],
        "stage_rows": stage_rows,
        "failures": failures,
        "file_comparison": file_comparison,
        "profile_rows": profile_rows,
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
        "signoff": "Production sign-off is NOT granted by this run",
    }


def write_migration_pdf(
    out_dir,
    summary: dict,
    rule_results=None,
    test_outcomes=None,
    filename: "str | None" = None,
) -> Path:
    """Render the migration report next to the other run artifacts."""
    review = build_report(summary, rule_results, test_outcomes)
    out = Path(out_dir) / (filename or REPORT_NAME)
    status = review["status"]

    title = ParagraphStyle("title", fontSize=16, leading=20, spaceAfter=2 * mm)
    h = ParagraphStyle("h", fontSize=11, leading=14, spaceBefore=5 * mm, spaceAfter=2 * mm)
    body = ParagraphStyle("body", fontSize=9, leading=12)
    small = ParagraphStyle("small", fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
    mono = ParagraphStyle(
        "mono", fontName="Courier", fontSize=7, leading=9, textColor=colors.HexColor("#333333")
    )
    right = ParagraphStyle("right", fontSize=9, leading=12, alignment=TA_RIGHT)

    def esc(text):
        safe = (
            str(text)
            .replace("→", "->")
            .replace("—", "-")
            .replace("–", "-")
            .replace("·", "|")
            .replace("…", "...")
        )
        return safe.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def P(text, style=body):
        return Paragraph(esc(text), style)

    def PM(text, style=body):
        return Paragraph(text, style)

    story = [
        Paragraph("Migration Summary Report", title),
        Paragraph("Test results, failure reasons and proof for QA and reviewers", small),
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
        story += [Spacer(1, 2 * mm), PM(f"<b>Follow-up:</b> {esc(review['action'])}")]
    if review["skipped"]:
        story.append(
            P(
                "Did not run: " + ", ".join(review["skipped"]) + " - see the QA report for why.",
                small,
            )
        )

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

    # ---- Mapping workbook: what each dataset was supposed to do ---------------
    mappings = meta.get("mappings") or []
    if mappings:
        story += [Paragraph("Mapping workbook", h)]
        for m in mappings:
            overview = table(
                ["Mapping ID", "Source", "Target", "Key Columns", "Enabled"],
                [
                    [
                        P(m.get("mapping_id", ""), small),
                        P(m.get("source", ""), small),
                        P(m.get("target", ""), small),
                        P(", ".join(str(k) for k in m.get("keys") or []), small),
                        P(m.get("enabled", ""), small),
                    ]
                ],
                [42 * mm, 40 * mm, 40 * mm, 30 * mm, 18 * mm],
            )
            fields = m.get("fields") or []
            field_table = table(
                ["S.No", "Source Column", "Target Column", "Type", "Nullable", "Transformation"],
                [
                    [
                        str(i),
                        P(f.get("source", ""), small),
                        P(f.get("target", ""), small),
                        P(f.get("type", ""), small),
                        P(f.get("nullable", ""), small),
                        P(f.get("transformation", ""), small),
                    ]
                    for i, f in enumerate(fields, 1)
                ],
                [9 * mm, 26 * mm, 36 * mm, 14 * mm, 15 * mm, 70 * mm],
            )
            story += [overview, field_table, Spacer(1, 3 * mm)]

    # ---- Executed test cases ------------------------------------------------
    story += [Paragraph("Test cases", h)]
    if review["cases"]:
        rows = [
            [c["case_id"], P(c["name"], small), P(c["result"], small), P(c["evidence"], small)]
            for c in review["cases"]
        ]
        case_table = table(
            ["ID", "Test case", "Result", "Evidence"],
            rows,
            [14 * mm, 78 * mm, 20 * mm, 58 * mm],
        )
        for r, c in enumerate(review["cases"], 1):
            with contextlib.suppress(KeyError):
                case_table.setStyle(
                    TableStyle([("BACKGROUND", (2, r), (2, r), _STATUS_FILL[c["result"]])])
                )
        story.append(case_table)
    else:
        story.append(P("No test results were recorded.", small))

    # ---- Every recorded stage check ------------------------------------------
    story += [Paragraph("All migration checks (per dataset, per stage)", h)]
    if review["stage_rows"]:
        rows = [
            [
                P(s["mapping"], small),
                P(s["stage"], small),
                P(s["status"], small),
                P(s["src"], small),
                P(s["tgt"], small),
                P(s["mismatches"], small),
                P(s["seconds"], small),
            ]
            for s in review["stage_rows"]
        ]
        stage_table = table(
            ["Dataset", "Check", "Status", "Src rows", "Tgt rows", "Mismatches", "Secs"],
            rows,
            [34 * mm, 42 * mm, 18 * mm, 20 * mm, 20 * mm, 22 * mm, 14 * mm],
        )
        for r, s in enumerate(review["stage_rows"], 1):
            with contextlib.suppress(KeyError):
                stage_table.setStyle(
                    TableStyle([("BACKGROUND", (2, r), (2, r), _STATUS_FILL[s["status"]])])
                )
        story.append(stage_table)
    else:
        story.append(P("No stage checks were recorded.", small))

    # ---- Negative self-test: source vs defect-copy file comparison ------------
    if review["file_comparison"]:
        src_label = (meta.get("source_type") or "source").upper()
        story += [
            Paragraph("File comparison - negative self-test", h),
            P(
                "The defect self-test recompares the source against streamed copies "
                "carrying one injected defect each, and expects the exact difference.",
                small,
            ),
        ]
        rows = [
            [
                P(fc["mapping"], small),
                P(fc.get("variant", ""), small),
                P(fc.get("source_rows", ""), small),
                P(fc.get("negative_rows", ""), small),
                P(fc.get("row_difference", ""), small),
                P("True" if fc.get("columns_match") else "False", small),
            ]
            for fc in review["file_comparison"]
        ]
        story.append(
            table(
                [
                    "Dataset",
                    "Variant",
                    f"Source {src_label} Rows",
                    f"Negative {src_label} Rows",
                    "Row Difference",
                    "Columns Match",
                ],
                rows,
                [40 * mm, 34 * mm, 28 * mm, 30 * mm, 22 * mm, 16 * mm],
            )
        )

    # ---- Column profile -------------------------------------------------------
    if review["profile_rows"]:
        story += [Paragraph("Column profile", h)]
        rows = [
            [
                P(p["mapping"], small),
                P(p["column"], small),
                P(p["src_nulls"], small),
                P(p["tgt_nulls"], small),
                P(p["src_distinct"], small),
                P(p["tgt_distinct"], small),
            ]
            for p in review["profile_rows"]
        ]
        prof_table = table(
            ["Dataset", "Column", "Src Nulls", "Tgt Nulls", "Src Distinct", "Tgt Distinct"],
            rows,
            [44 * mm, 36 * mm, 20 * mm, 20 * mm, 25 * mm, 25 * mm],
        )
        for r, p in enumerate(review["profile_rows"], 1):
            if p["differs"]:
                prof_table.setStyle(
                    TableStyle([("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FFC7CE"))])
                )
        story.append(prof_table)

    # ---- Failures: reason + proof ---------------------------------------------
    if review["failures"]:
        story += [Paragraph("Failures - reason and proof", h)]
        tiny = ParagraphStyle("tiny", fontSize=5.5, leading=7)
        for f in review["failures"]:
            block = [PM(f"<b>{esc(f['heading'])}</b>")]
            if f["reason"]:
                block.append(P(f"Reason: {f['reason']}", small))
            for line in f["proof"]:
                block.append(P(f"  {line}", mono))
            if not f["reason"] and not f["proof"] and not f["records"]:
                block.append(P("No detail recorded - see migration_summary.xlsx.", small))
            story.append(KeepTogether(block))

            if f["records"]:
                label = f"Problem records - table {f['table']}" if f["table"] else "Problem records"
                if f["records_total"] > len(f["records"]):
                    label += f" (first {len(f['records'])} of {f['records_total']})"
                story.append(P(label, small))

                # key / FK / date / differing columns only — full 17-column
                # records are noise; anything wider than the page still chunks
                rec_cols = f["record_columns"]
                for start in range(0, len(rec_cols), 8):
                    cols = rec_cols[start : start + 8]
                    if len(rec_cols) > 8:
                        story.append(
                            P(f"columns {start + 1}-{start + len(cols)} of {len(rec_cols)}", tiny)
                        )
                    data = [["S.No", "Side", "Key"] + [P(c, tiny) for c in cols]]
                    widths = [9 * mm, 12 * mm, 14 * mm] + [135 / len(cols) * mm] * len(cols)
                    spans, fills = [], []
                    for sno, (key, sides) in enumerate(f["records"], 1):
                        src_row = tgt_row = None
                        for side in ("source", "target"):
                            idx = len(data)
                            rec = sides.get(side)
                            if side == "source":
                                src_row = idx
                            else:
                                tgt_row = idx
                            if rec is None:
                                data.append(
                                    [str(sno), side, str(key), "(absent)"] + [""] * (len(cols) - 1)
                                )
                                spans.append(("SPAN", (3, idx), (-1, idx)))
                            else:
                                data.append(
                                    [str(sno), side, str(key)]
                                    + [P(str(rec.get(c, "")), tiny) for c in cols]
                                )
                        if src_row and tgt_row and sides.get("source") and sides.get("target"):
                            for j, c in enumerate(cols):
                                if sides["source"].get(c) != sides["target"].get(c):
                                    fills += [
                                        (
                                            "BACKGROUND",
                                            (3 + j, src_row),
                                            (3 + j, src_row),
                                            colors.HexColor("#FFC7CE"),
                                        ),
                                        (
                                            "BACKGROUND",
                                            (3 + j, tgt_row),
                                            (3 + j, tgt_row),
                                            colors.HexColor("#FFC7CE"),
                                        ),
                                    ]

                    rec_table = Table(data, colWidths=widths, repeatRows=1)
                    rec_table.setStyle(
                        TableStyle(
                            [
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                ("FONTSIZE", (0, 0), (-1, -1), 6),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                (
                                    "ROWBACKGROUNDS",
                                    (0, 1),
                                    (-1, -1),
                                    [colors.white, colors.HexColor("#F7F7F7")],
                                ),
                            ]
                        )
                    )
                    if spans or fills:
                        rec_table.setStyle(TableStyle(spans + fills))
                    story += [rec_table, Spacer(1, 1 * mm)]
                story.append(Spacer(1, 1 * mm))
            else:
                story.append(Spacer(1, 2 * mm))

    # ---- Business rules ---------------------------------------------------------
    if review["rule_rows"]:
        story += [Paragraph("Business rules", h)]
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

    story += [
        Spacer(1, 4 * mm),
        HRFlowable(color=colors.HexColor("#BBBBBB"), thickness=0.5),
        Paragraph("How to review", h),
        P(
            "PASS means every check above ran and found no difference. FAIL lists each failing "
            "check with its reason and bounded proof samples; the full defect list is in "
            "migration_summary.xlsx (Diffs & Violations sheet). INCOMPLETE or REVIEW needs a QA "
            "look before any sign-off."
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
        title="Migration Summary Report",
        author="migration verification framework",
    )
    doc.build(story)
    return out
