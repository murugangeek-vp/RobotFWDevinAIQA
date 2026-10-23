"""Consolidated end-to-end lifecycle report — one workbook + one markdown file.

Merges the migration stage evidence (the structure written to
run_summary.json) and the business-rules RuleResult list into a single
reviewer-facing artifact: end_to_end_summary.xlsx (Run Info | Stages |
Controls | Rules | Violations) and end_to_end_report.md. Engine outputs are
inputs here — this module contains no comparison logic.
"""

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

_FILL = {
    "PASS": "C6EFCE",
    "WARN": "FFEB9C",
    "FAIL": "FFC7CE",
    "ERROR": "FFC7CE",
    "NOT_RUN": "D9D9D9",
}


def _bold_header(ws) -> None:
    for c in ws[1]:
        c.font = Font(bold=True)


def _write_workbook(
    run: dict, rule_results: list, meta: dict, out: Path, trace: "dict | None" = None
) -> Path:
    results = run.get("results") or []
    stage_fail = sum(1 for r in results if r.get("status") != "PASS")
    rule_fail = sum(1 for r in rule_results if r.status in ("FAIL", "ERROR"))
    n_viol = sum(len(r.violations) for r in rule_results)
    overall = (
        "FAIL"
        if run.get("verdict") == "FAIL" or stage_fail or rule_fail
        else ("NOT_RUN" if not results and not rule_results else "PASS")
    )

    wb = Workbook()
    info = wb.active
    info.title = "Run Info"
    info.append(["Field", "Value"])
    _bold_header(info)
    for field, value in [
        ("Run Timestamp", meta.get("timestamp", "")),
        ("Environment", meta.get("environment", "")),
        ("Pipeline / Hop", meta.get("hop", "")),
        ("Mapping Workbook", meta.get("mapping_workbook", "")),
        ("Snapshot Id", (run.get("cutover") or {}).get("snapshot_id", "")),
        ("Source Snapshot", (run.get("cutover") or {}).get("source_snapshot", "")),
        ("Snapshot Enforced", (run.get("cutover") or {}).get("snapshot_enforced", "")),
        ("Executed By", meta.get("executed_by", "")),
        ("Git Commit", meta.get("git_commit", "")),
        ("Stage Checks", f"{len(results)} run, {stage_fail} not passed"),
        ("Business Rules", f"{len(rule_results)} run, {rule_fail} not passed"),
        ("Rule Violations", n_viol),
        (
            "Requirements Covered",
            (
                f"{len({r['req'] for r in trace['rtm_rows']}) - len(trace['uncovered'])}"
                f" covered, {len(trace['uncovered'])} uncovered"
                if trace
                else ""
            ),
        ),
        (
            "DC/DA/DI/DG",
            (" | ".join(f"{d}:{v}" for d, v in trace["verdicts"].items()) if trace else ""),
        ),
        ("Verdict", run.get("verdict", "")),
        ("Coverage", run.get("coverage", "")),
        ("Overall Status", overall),
        ("Production Signoff", "NOT GRANTED: real account and snapshot validation required"),
    ]:
        info.append([field, value])
        info.cell(row=info.max_row, column=1).font = Font(bold=True)
    info.cell(row=info.max_row - 1, column=2).fill = PatternFill("solid", fgColor=_FILL[overall])
    info.column_dimensions["A"].width = 20
    info.column_dimensions["B"].width = 80

    st = wb.create_sheet("Stages")
    st.append(["Mapping", "Stage", "Status", "Source Rows", "Target Rows", "Seconds", "Detail"])
    _bold_header(st)
    for r in results:
        st.append(
            [
                r.get("mapping", ""),
                r.get("stage", ""),
                r.get("status", ""),
                r.get("source_count", ""),
                r.get("target_count", ""),
                r.get("seconds", ""),
                r.get("detail", ""),
            ]
        )
        st.cell(row=st.max_row, column=3).fill = PatternFill(
            "solid", fgColor=_FILL.get(r.get("status", ""), "FFFFFF")
        )
    for i, w in enumerate([24, 12, 10, 12, 12, 9, 90], start=1):
        st.column_dimensions[get_column_letter(i)].width = w
    st.freeze_panes = "A2"

    ct = wb.create_sheet("Controls")
    ct.append(["Mapping", "Control", "Group", "Expected", "Actual"])
    _bold_header(ct)
    for r in results:
        if r.get("stage") != "controls":
            continue
        mismatches = r.get("control_mismatches") or []
        if not mismatches:
            ct.append([r.get("mapping", ""), "(all controls certified)", "", "", ""])
        for m in mismatches:
            if isinstance(m, dict):
                ct.append(
                    [
                        r.get("mapping", ""),
                        m.get("control", ""),
                        m.get("group", ""),
                        m.get("expected", ""),
                        m.get("actual", ""),
                    ]
                )
    for i, w in enumerate([24, 34, 24, 24, 24], start=1):
        ct.column_dimensions[get_column_letter(i)].width = w
    ct.freeze_panes = "A2"

    rs = wb.create_sheet("Rules")
    rs.append(
        [
            "Test ID",
            "Rule Type",
            "Business Rule",
            "Severity",
            "Status",
            "Violations",
            "Query",
            "Error",
        ]
    )
    _bold_header(rs)
    for r in rule_results:
        rule = r.rule
        rs.append(
            [
                rule.test_id,
                rule.rule_type,
                rule.business_rule or "",
                rule.severity,
                r.status,
                len(r.violations),
                r.sql or "",
                r.error or "",
            ]
        )
        rs.cell(row=rs.max_row, column=5).fill = PatternFill(
            "solid", fgColor=_FILL.get(r.status, "FFFFFF")
        )
    for i, w in enumerate([22, 15, 60, 10, 8, 10, 70, 40], start=1):
        rs.column_dimensions[get_column_letter(i)].width = w
    rs.freeze_panes = "A2"

    vs = wb.create_sheet("Violations")
    vs.append(["Test ID", "Severity", "Column", "Key", "Expected", "Actual"])
    _bold_header(vs)
    for r in rule_results:
        for v in r.violations:
            vs.append(
                [
                    r.rule.test_id,
                    r.rule.severity,
                    v.column,
                    str(v.key),
                    str(v.expected),
                    str(v.actual),
                ]
            )
    for i, w in enumerate([22, 10, 24, 20, 40, 40], start=1):
        vs.column_dimensions[get_column_letter(i)].width = w
    vs.freeze_panes = "A2"

    if trace and trace.get("rtm_rows"):
        rt = wb.create_sheet("RTM")
        rt.append(["Requirement ID", "Artifact", "Check", "Status"])
        _bold_header(rt)
        for r in trace["rtm_rows"]:
            rt.append([r["req"], r["artifact"], r["check"], r["status"]])
            rt.cell(row=rt.max_row, column=4).fill = PatternFill(
                "solid", fgColor=_FILL.get(r["status"], "FFFFFF")
            )
        for i, w in enumerate([18, 44, 30, 14], start=1):
            rt.column_dimensions[get_column_letter(i)].width = w
        rt.freeze_panes = "A2"

    if trace and trace.get("scorecard_rows"):
        sc = wb.create_sheet("Scorecard")
        sc.append(["Dimension", "Check", "Status", "Evidence"])
        _bold_header(sc)
        for r in trace["scorecard_rows"]:
            sc.append([r["dimension"], r["check"], r["status"], r["evidence"]])
            sc.cell(row=sc.max_row, column=3).fill = PatternFill(
                "solid", fgColor=_FILL.get(r["status"], "FFFFFF")
            )
        for i, w in enumerate([12, 44, 10, 60], start=1):
            sc.column_dimensions[get_column_letter(i)].width = w
        sc.freeze_panes = "A2"

    # --- QA diagnostic sheets (same detail migration_summary.xlsx carries) ---
    dq = wb.create_sheet("Data Diffs")
    dq.append(["Mapping", "Stage", "Type", "Key / Name", "Column", "Expected", "Actual"])
    _bold_header(dq)
    seen = set()

    def defect(row, kind, key="", column="", expected="", actual=""):
        entry = (row["mapping"], row["stage"], kind, key, column, expected, actual)
        if entry not in seen:
            seen.add(entry)
            dq.append(list(entry))

    for row in results:
        for k in row.get("missing_samples") or []:
            defect(row, "missing_in_target", str(k), actual="absent in target")
        for k in row.get("extra_samples") or []:
            defect(row, "extra_in_target", str(k), actual="absent in source")
        for d in row.get("diff_samples") or []:
            defect(
                row,
                "value_diff",
                str(d.get("key", "")),
                d.get("column", ""),
                str(d.get("expected", "")),
                str(d.get("actual", "")),
            )
        for m in row.get("control_mismatches") or []:
            if isinstance(m, dict):
                defect(
                    row,
                    "control_mismatch",
                    str(m.get("control", "")),
                    str(m.get("group", "")),
                    str(m.get("expected", "")),
                    str(m.get("actual", "")),
                )
        for d in row.get("profile_diffs") or []:
            defect(
                row,
                "profile_diff",
                str(d.get("column", "")),
                str(d.get("stat", d.get("issue", ""))),
                str(d.get("source", "")),
                str(d.get("target", "")),
            )
        for e in row.get("schema_errors") or []:
            defect(row, "schema_error", actual=str(e))
        for rid, n in (row.get("dq_failures") or {}).items():
            if isinstance(n, list):
                defect(row, "dq_samples", rid, actual=", ".join(str(x) for x in n))
            else:
                defect(row, "dq_failure", rid, actual=f"{n} failing row(s)")
        for t in row.get("unmapped_source_tables") or []:
            defect(row, "unmapped_source_table", str(t), actual="no workbook mapping")
        for t in row.get("unmapped_target_tables") or []:
            defect(row, "unmapped_target_table", str(t), actual="no workbook mapping")
        for e in row.get("not_validated") or []:
            defect(row, "not_validated", actual=str(e))
    for i, w in enumerate([24, 12, 20, 24, 24, 40, 40], start=1):
        dq.column_dimensions[get_column_letter(i)].width = w
    dq.freeze_panes = "A2"

    qs = wb.create_sheet("Queries")
    qs.append(["Mapping", "Stage", "Source Query", "Target Query"])
    _bold_header(qs)
    seen_q = set()
    for row in results:
        pair = (row.get("source_query", ""), row.get("target_query", ""))
        if any(pair) and (row["mapping"],) + pair not in seen_q:
            seen_q.add((row["mapping"],) + pair)
            qs.append([row["mapping"], row["stage"], pair[0], pair[1]])
    for i, w in enumerate([24, 12, 60, 60], start=1):
        qs.column_dimensions[get_column_letter(i)].width = w
    qs.freeze_panes = "A2"

    cp = wb.create_sheet("Column Profile")
    cp.append(
        [
            "Mapping",
            "Column",
            "Src Nulls",
            "Tgt Nulls",
            "Src Distinct",
            "Tgt Distinct",
            "Src Min",
            "Tgt Min",
            "Src Max",
            "Tgt Max",
        ]
    )
    _bold_header(cp)
    seen_p = set()
    for row in results:
        for p in row.get("column_profile") or []:
            vals = (
                row["mapping"],
                p["column"],
                p["src_nulls"],
                p["tgt_nulls"],
                p["src_distinct"],
                p["tgt_distinct"],
                p["src_min"],
                p["tgt_min"],
                p["src_max"],
                p["tgt_max"],
            )
            if vals not in seen_p:
                seen_p.add(vals)
                cp.append(list(vals))
    for i, w in enumerate([24, 24, 10, 10, 12, 12, 16, 16, 16, 16], start=1):
        cp.column_dimensions[get_column_letter(i)].width = w
    cp.freeze_panes = "A2"

    md = wb.create_sheet("Metadata")
    md.append(["Mapping", "Stage", "Source", "Target", "Observed Metadata"])
    _bold_header(md)
    for row in results:
        observed = {
            k: row[k]
            for k in (
                "source_columns",
                "source_metadata",
                "target_metadata",
                "field_mappings",
                "unmapped_source_tables",
                "unmapped_target_tables",
                "relation",
                "source_orphans",
                "source_null_fk",
                "target_orphans",
                "target_null_fk",
                "post_snapshot_arrivals",
                "target_asof",
            )
            if k in row
        }
        if observed:
            md.append(
                [
                    row["mapping"],
                    row["stage"],
                    row.get("source", ""),
                    row.get("target", ""),
                    json.dumps(observed, default=str),
                ]
            )
    for i, w in enumerate([24, 12, 30, 30, 90], start=1):
        md.column_dimensions[get_column_letter(i)].width = w
    md.freeze_panes = "A2"

    ins = wb.create_sheet("Insights")
    ins.append(["Insight / Follow-up", "Detail"])
    _bold_header(ins)
    from libs.reporting.report import build_evidence_insights

    for item in build_evidence_insights(
        run.get("meta") or {},
        run.get("cutover") or {},
        run.get("not_run") or {},
        run.get("verdict", ""),
    ):
        ins.append(["Insight / follow-up", item])
    ins.column_dimensions["A"].width = 22
    ins.column_dimensions["B"].width = 120
    ins.freeze_panes = "A2"

    path = out / "end_to_end_summary.xlsx"
    wb.save(path)
    return path


def _write_markdown(
    run: dict, rule_results: list, meta: dict, out: Path, trace: "dict | None" = None
) -> Path:
    results = run.get("results") or []
    lines = [
        "# End-to-End Migration Validation Report",
        "",
        f"- environment: {meta.get('environment', '')}",
        f"- timestamp: {meta.get('timestamp', '')}",
        f"- verdict: **{run.get('verdict', '')}** | coverage: {run.get('coverage', '')}",
        f"- snapshot: {(run.get('cutover') or {}).get('snapshot_id', '')} "
        f"({(run.get('cutover') or {}).get('source_snapshot', '')})",
        "",
        "## Stage results",
        "",
        "| Mapping | Stage | Status | Detail |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.get('mapping', '')} | {r.get('stage', '')} | {r.get('status', '')} "
        f"| {str(r.get('detail', '')).replace('|', '/')} |"
        for r in results
    ]
    lines += [
        "",
        "## Business rules",
        "",
        "| Test ID | Type | Status | Violations |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.rule.test_id} | {r.rule.rule_type} | {r.status} | {len(r.violations)} |"
        for r in rule_results
    ]
    if trace:
        covered = len({r["req"] for r in trace["rtm_rows"]}) - len(trace["uncovered"])
        lines += [
            "",
            "## Traceability & compliance",
            "",
            f"- requirements: {covered} covered, {len(trace['uncovered'])} uncovered"
            + (f" — {', '.join(trace['uncovered'])}" if trace["uncovered"] else ""),
            "- dimensions: " + " | ".join(f"{d} {v}" for d, v in trace["verdicts"].items()),
        ]
    fails = [r for r in results if r.get("status") != "PASS"] + [
        r for r in rule_results if r.status in ("FAIL", "ERROR")
    ]
    if fails:
        lines += ["", "## Failures", ""]
        for f in fails:
            if hasattr(f, "rule"):
                lines.append(
                    f"- rule {f.rule.test_id}: {f.status} — {len(f.violations)} violation(s)"
                )
                for v in f.violations[:5]:
                    lines.append(
                        f"  - key={v.key} {v.column}: expected {v.expected!r} actual {v.actual!r}"
                    )
            else:
                lines.append(
                    f"- {f.get('mapping', '')}/{f.get('stage', '')}: "
                    f"{f.get('status', '')} — {str(f.get('detail', ''))[:200]}"
                )
    path = out / "end_to_end_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_consolidated(
    run: dict, rule_results: list, out_dir, meta: "dict | None" = None, trace: "dict | None" = None
) -> Path:
    """Write end_to_end_summary.xlsx + end_to_end_report.md; returns the xlsx path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    written = _write_workbook(run, rule_results, meta, out, trace=trace)
    _write_markdown(run, rule_results, meta, out, trace=trace)
    return written
