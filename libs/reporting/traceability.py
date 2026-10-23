"""Requirement traceability, DC/DA/DI/DG compliance scorecard, defect report.

A thin reporting join over data the run already produced: parsed workbook
artifacts (tables/fields/controls/relations + their Requirement IDs),
stage evidence (run_summary.json contents) and business-rule results.
Contains no comparison logic — it only re-labels existing results.

Outputs per run: rtm.xlsx, compliance_scorecard.xlsx, defect_report.md
(defect report only when failures exist).
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

_FILL = {
    "PASS": "C6EFCE",
    "WARN": "FFEB9C",
    "FAIL": "FFC7CE",
    "ERROR": "FFC7CE",
    "UNCOVERED": "D9D9D9",
    "CONFIRMED": "C6EFCE",
    "NOT-COVERED": "D9D9D9",
}

# Stage -> which governance dimensions its evidence feeds (DC completeness,
# DA accuracy, DI integrity, DG governance).
_STAGE_DIM = {
    "mapping": ("DC",),
    "source": ("DC",),
    "dq": ("DI",),
    "schema": ("DI",),
    "profile": ("DA",),
    "recon": ("DC", "DA"),
    "controls": ("DC", "DA"),
    "transform": ("DA",),
    "relations": ("DI",),
}

# Rule type -> governance dimension.
_RULE_DIM = {
    "DIRECT_COMPARE": "DA",
    "LOOKUP": "DA",
    "CUSTOM_PYTHON": "DA",
    "MAP": "DA",
    "CONCAT": "DA",
    "CASE_WHEN": "DA",
    "SUBSTRING": "DA",
    "PREFIX": "DA",
    "SUFFIX": "DA",
    "CUSTOM_SQL": "DI",
    "REGEX": "DI",
    "NULL_CHECK": "DI",
    "DATE_FORMAT": "DI",
    "MAX_LENGTH": "DI",
    "MIN_LENGTH": "DI",
    "MASK": "DG",
    "HASH": "DG",
    "ENCRYPTION": "DG",
    "DECRYPTION": "DG",
}

# Failure triage owner hint per stage (used in defect_report.md).
_OWNER = {
    "mapping": "Requirements / BA",
    "source": "Extract provider",
    "dq": "Data steward",
    "schema": "Target platform",
    "profile": "Data steward",
    "recon": "ETL / migration",
    "controls": "Finance operations",
    "transform": "ETL / migration",
    "relations": "Data model",
    "rules": "Business owner",
}

_WORST = {"PASS": 0, "WARN": 1, "UNCOVERED": 1, "FAIL": 2, "ERROR": 2}


def _worst(statuses):
    return max(statuses, key=lambda s: _WORST.get(s, 2), default="UNCOVERED")


def build_rtm(tables, rules, rule_results, evidence):
    """Requirement ID -> check rows. Returns (rows, uncovered_req_ids)."""
    ev_by_map = {}
    for r in evidence:
        ev_by_map.setdefault(r.get("mapping"), []).append(r)
    result_by_rule = {r.rule.test_id: r for r in rule_results}
    rows = []

    def emit(req, artifact, check, status):
        rows.append({"req": req, "artifact": artifact, "check": check, "status": status})

    for t in tables:
        if not t.enabled:
            continue
        evs = ev_by_map.get(t.mapping_id, [])
        if t.requirement_id:
            if not evs:
                emit(t.requirement_id, t.mapping_id, "(no stage evidence)", "UNCOVERED")
            for e in evs:
                emit(t.requirement_id, t.mapping_id, f"stage:{e.get('stage')}", e.get("status"))
        # Field-level requirements ride the field-evaluating stages.
        field_evs = [e for e in evs if e.get("stage") in ("recon", "transform", "schema")]
        for f in t.fields:
            if f.enabled and f.requirement_id:
                if not field_evs:
                    emit(
                        f.requirement_id,
                        f"{t.mapping_id}.{f.target_column}",
                        "(no field evidence)",
                        "UNCOVERED",
                    )
                for e in field_evs:
                    emit(
                        f.requirement_id,
                        f"{t.mapping_id}.{f.target_column}",
                        f"stage:{e.get('stage')}",
                        e.get("status"),
                    )
        for c in t.controls:
            req = c.get("requirement_id")
            if not req:
                continue
            ctrl_ev = next((e for e in evs if e.get("stage") == "controls"), None)
            if ctrl_ev is None:
                emit(req, f"{t.mapping_id}.{c['id']}", "controls stage", "UNCOVERED")
                continue
            mismatched = any(
                m.get("control") == c["id"] for m in (ctrl_ev.get("control_mismatches") or [])
            )
            emit(
                req,
                f"{t.mapping_id}.{c['id']}",
                "certified control",
                "FAIL" if mismatched else ctrl_ev.get("status"),
            )
        for rel in t.relations:
            req = rel.get("requirement_id")
            if not req:
                continue
            rel_ev = next(
                (e for e in ev_by_map.get(t.mapping_id, []) if e.get("stage") == "relations"),
                None,
            )
            emit(
                req,
                f"{t.mapping_id}.{rel['child_column']}→{rel['parent_mapping']}",
                "FK relation",
                rel_ev.get("status") if rel_ev else "UNCOVERED",
            )

    for r in rules:
        if not (r.enabled and r.requirement_id):
            continue
        res = result_by_rule.get(r.test_id)
        emit(r.requirement_id, r.test_id, "business rule", res.status if res else "UNCOVERED")

    # Per-requirement verdict = worst status across its checks.
    uncovered = sorted({row["req"] for row in rows if row["status"] == "UNCOVERED"})
    return rows, uncovered


def build_scorecard(tables, rule_results, evidence, run):
    """DC/DA/DI/DG scorecard rows + per-dimension verdict."""
    rows = []
    for e in evidence:
        for dim in _STAGE_DIM.get(e.get("stage", ""), ()):
            rows.append(
                {
                    "dimension": dim,
                    "check": f"{e.get('mapping')}/{e.get('stage')}",
                    "status": e.get("status"),
                    "evidence": str(e.get("detail", ""))[:120],
                }
            )
    for r in rule_results:
        dim = _RULE_DIM.get(r.rule.rule_type, "DA")
        rows.append(
            {
                "dimension": dim,
                "check": f"rule:{r.rule.test_id}",
                "status": r.status,
                "evidence": (r.rule.business_rule or r.error or "")[:120],
            }
        )
    cutover = run.get("cutover") or {}
    meta = run.get("meta") or {}
    pii = sum(1 for t in tables for f in t.fields if f.pii)
    rows += [
        {
            "dimension": "DG",
            "check": "snapshot enforcement",
            "status": "PASS" if cutover.get("snapshot_enforced") else "FAIL",
            "evidence": f"source_snapshot={cutover.get('source_snapshot', '')}",
        },
        {
            "dimension": "DG",
            "check": "PII columns declared",
            "status": "PASS" if pii else "WARN",
            "evidence": f"{pii} FieldMap rows flagged PII",
        },
        {
            "dimension": "DG",
            "check": "audit metadata",
            "status": "PASS" if (meta.get("started") or meta.get("snapshot_id")) else "WARN",
            "evidence": "timestamp/environment/commit recorded in run summary",
        },
    ]
    verdicts = {}
    for dim in ("DC", "DA", "DI", "DG"):
        sts = [r["status"] for r in rows if r["dimension"] == dim]
        verdicts[dim] = (
            "NOT-COVERED"
            if not sts
            else ("FAILED" if any(s in ("FAIL", "ERROR") for s in sts) else "CONFIRMED")
        )
    return rows, verdicts


def build_defects(tables, rule_results, evidence):
    """Failure detail for defect_report.md — counts, keys, owners, req links."""
    req_of_map = {t.mapping_id: t.requirement_id for t in tables}
    defects = []
    for e in evidence:
        if e.get("status") == "PASS":
            continue
        defects.append(
            {
                "check": f"{e.get('mapping')}/{e.get('stage')}",
                "status": e.get("status"),
                "req": req_of_map.get(e.get("mapping"), ""),
                "owner": _OWNER.get(e.get("stage", ""), ""),
                "detail": str(e.get("detail", ""))[:200],
                "samples": e.get("control_mismatches") or [],
            }
        )
    for r in rule_results:
        if r.status in ("FAIL", "ERROR"):
            defects.append(
                {
                    "check": f"rule:{r.rule.test_id}",
                    "status": r.status,
                    "req": r.rule.requirement_id or "",
                    "owner": _OWNER["rules"],
                    "detail": r.error or f"{len(r.violations)} violation(s)",
                    "samples": [
                        f"key={v.key} {v.column}: expected {v.expected!r} actual {v.actual!r}"
                        for v in r.violations[:10]
                    ],
                    "sql": r.sql or "",
                }
            )
    return defects


def _write_sheet(wb, title, headers, rows, widths, status_col):
    ws = (
        wb.active
        if wb.active.max_row == 1 and wb.active["A1"].value is None
        else wb.create_sheet(title)
    )
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws.append(row)
        ws.cell(row=ws.max_row, column=status_col).fill = PatternFill(
            "solid", fgColor=_FILL.get(str(row[status_col - 1]).upper(), "FFFFFF")
        )
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def write_trace(tables, rules, rule_results, run, out_dir):
    """Emit rtm.xlsx + compliance_scorecard.xlsx (+ defect_report.md on
    failures). Returns the summary dict for callers/augmentation."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    evidence = run.get("results") or []
    rtm_rows, uncovered = build_rtm(tables, rules, rule_results, evidence)
    sc_rows, verdicts = build_scorecard(tables, rule_results, evidence, run)
    defects = build_defects(tables, rule_results, evidence)

    if rtm_rows:
        wb = Workbook()
        _write_sheet(
            wb,
            "RTM",
            ["Requirement ID", "Artifact", "Check", "Status"],
            [[r["req"], r["artifact"], r["check"], r["status"]] for r in rtm_rows],
            [18, 44, 30, 14],
            4,
        )
        wb.save(out / "rtm.xlsx")

    wb = Workbook()
    _write_sheet(
        wb,
        "Scorecard",
        ["Dimension", "Check", "Status", "Evidence"],
        [[r["dimension"], r["check"], r["status"], r["evidence"]] for r in sc_rows],
        [12, 44, 10, 60],
        3,
    )
    vs = wb.create_sheet("Dimension Verdicts")
    vs.append(["Dimension", "Verdict"])
    for c in vs[1]:
        c.font = Font(bold=True)
    names = {
        "DC": "Data Completeness",
        "DA": "Data Accuracy",
        "DI": "Data Integrity",
        "DG": "Data Governance",
    }
    for dim, verdict in verdicts.items():
        vs.append([f"{dim} — {names[dim]}", verdict])
        vs.cell(row=vs.max_row, column=2).fill = PatternFill(
            "solid", fgColor=_FILL.get(verdict, "FFFFFF")
        )
    vs.column_dimensions["A"].width = 30
    vs.column_dimensions["B"].width = 14
    wb.save(out / "compliance_scorecard.xlsx")

    defect_path = None
    if defects:
        lines = ["# Defect Report", ""]
        for d in defects:
            lines.append(
                f"## {d['check']} — {d['status']}" + (f" (req {d['req']})" if d["req"] else "")
            )
            lines.append(f"- owner: {d['owner'] or 'unassigned'}")
            lines.append(f"- {d['detail']}")
            for s in d["samples"][:10]:
                lines.append(f"  - {s}")
            if d.get("sql"):
                lines.append(f"- audit SQL: `{d['sql'][:200]}`")
            lines.append("")
        defect_path = out / "defect_report.md"
        defect_path.write_text("\n".join(lines), encoding="utf-8")
    else:
        # A clean run must not leave a stale defect report behind.
        (out / "defect_report.md").unlink(missing_ok=True)

    return {
        "rtm_rows": rtm_rows,
        "scorecard_rows": sc_rows,
        "verdicts": verdicts,
        "uncovered": uncovered,
        "defects": len(defects),
        "files": {
            "rtm": str(out / "rtm.xlsx") if rtm_rows else None,
            "scorecard": str(out / "compliance_scorecard.xlsx"),
            "defects": str(defect_path) if defect_path else None,
        },
    }
