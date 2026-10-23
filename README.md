# QA Automation — Banking Data Migration Validation Framework

Robot Framework + Python framework that proves a bank data migration is
100% correct: every row arrived, every column value matches, every
transformation recomputes, every certified control holds — read-only on
both sides, with auditable evidence per hop.

## Phase-1 showcase

Three-hop pipeline per the client spec `data/trailer/Phase1.xlsx`:

| Hop | Source → Target |
|---|---|
| 1 | CSV extracts (one file per table) → `wdr_uat_active_stage.signature` |
| 2 | `signature` → `wdr_uat_integration.wdr_il_core` |
| 3 | `wdr_il_core` → `wdr_uat_consumption.wdr_cl_fds_core` |

One hop = one environment YAML + one mapping workbook — the workbook is
the single input template (Tables, FieldMap, Controls, Relations,
Mapping, plus a `Rules` sheet of business rules with plain-English
`Business Rule` explanations). `tests/migration/01_end_to_end_migration_testing.robot`
runs the whole chain as five end-to-end-lifecycle tests and writes one
consolidated report: **`end_to_end_report.pdf`** is the plain-English review
copy for QA, managers, support and clients. It shows test cases, results,
record-count evidence, business-rule outcomes and follow-up actions.
`end_to_end_summary.xlsx` retains detailed QA diagnostics, with
`end_to_end_report.md` as a text summary;
`90_negative_path.robot` is the exact-count defect-detection proof.

- **Runbook:** [docs/RUNBOOK.md](docs/RUNBOOK.md) — setup, credentials,
  full generate→load→verify chain, troubleshooting.
- **Workflow:** [docs/WORKFLOW.md](docs/WORKFLOW.md) — operator flow,
  evidence map, failure triage, UAT onboarding.
- **Workbook spec:** [docs/EXCEL_MAPPING.md](docs/EXCEL_MAPPING.md) —
  sheet formats, transform language, fail-closed rules.
- **Adapters:** [docs/ADAPTERS.md](docs/ADAPTERS.md) — any-database
  extension points.
- **Plans:** [docs/IMPLEMENTATION_PLAN_PHASE2.md](docs/IMPLEMENTATION_PLAN_PHASE2.md),
  [docs/IMPLEMENTATION_PLAN_PHASE3.md](docs/IMPLEMENTATION_PLAN_PHASE3.md)
  (end-to-end lifecycle consolidation).

All fixtures are synthetic and deterministic; verification runs under a
SELECT-only role; no secrets are committed.
