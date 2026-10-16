---
name: recon-run
description: Run the migration validation suites (full, single, tagged, per-hop, negative-path) and interpret results
argument-hint: "[hop|suite|test] [--negative]"
allowed-tools:
  - read
  - grep
  - glob
  - exec
---

RUN THE MIGRATION VALIDATION SUITES

All commands run from the repo root using the venv executables
(`.venv/Scripts/robot`, `.venv/Scripts/python` on Windows). Prereq: the
three `wdr_uat_*` PostgreSQL databases provisioned + `.env` filled (see
docs/RUNBOOK.md). Verification is read-only (`recon_ro`); only the loader
script uses `recon_rw`.

## Phase-1 three-hop pipeline

One env + one workbook per hop; the same `tests/migration` suites run all:

1. **Hop 1 — CSV → landing:**
   `robot -d results/phase1/hop1 -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration`
2. **Hop 2 — landing → integration:**
   `robot -d results/phase1/hop2 -v MIGRATION_ENV:config/environments/phase1_hop2_stage_il.yaml -v MAPPING_WORKBOOK:data/mapping/phase1_hop2_stage_to_il.xlsx tests/migration`
3. **Hop 3 — integration → consumption:**
   `robot -d results/phase1/hop3 -v MIGRATION_ENV:config/environments/phase1_hop3_il_consumption.yaml -v MAPPING_WORKBOOK:data/mapping/phase1_hop3_il_to_consumption.xlsx tests/migration`

All three PASS → expect 11/11 each, rc=0.

## Pick a narrower run

- **One suite:** `robot -d results/x -v MIGRATION_ENV:<env>.yaml -v MAPPING_WORKBOOK:<wb>.xlsx tests/migration/04_record_comparison.robot`
- **One mapping:** add `-v MAPPING_ID:dim_customer_migration`
- **Compare+transform only:** `robot -d results/r --include recon tests/migration`
- **Negative path (prove detection):**
  `robot -d results/phase1/hop1_negative -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage_negative.yaml -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration`
  — expected FAIL: corrupted CSV fixtures must surface as exact counts
  (recon `extra: 5`, trailer control FAILs, FK orphans). A passing negative
  run is itself a defect.
- **Engine unit tests:**
  `python -m unittest discover -s tests/migration -p "test_*.py" -v`

## Evidence

Per run dir: `migration_summary.xlsx` (audit — Run Info / Stage Results /
Diffs & Violations), `migration_report.md`, `run_summary.json`
(`pipeline_id`, `hop`, `snapshot_id`, git commit, `verdict`,
`coverage` — must be COMPLETE), Robot `report.html`/`log.html`.
`production_signoff` is always false — a green run is not sign-off.

## Interpret failures (map to layer first)

| Failed stage | Layer to check |
| --- | --- |
| mapping / source | workbook compile — spec gaps fail closed here |
| dq | source rule violations — `dq_failures` in evidence |
| schema | target DDL drift — `schema_errors` |
| recon / transform | `missing`/`extra`/per-column diffs — bounded redacted samples |
| controls | trailer certification — `control_mismatches` |
| relations | FK orphans on either side of a mapping relation |
| negative | detection regression if tampered copies PASS |
| `permission denied` / auth failed | wrong role — verify is `recon_ro`, loader `recon_rw` |

## Report

- Summarize: hop, environment, per-stage counts, mismatch count, final
  PASS/FAIL — the five headline metrics.
- On FAIL: name the layer, the exact mismatch counts, and the first likely
  cause. Never report a reconciliation result as "close enough" — a count
  is either exact or it is a defect.
