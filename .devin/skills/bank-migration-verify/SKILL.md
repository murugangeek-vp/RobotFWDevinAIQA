---
name: bank-migration-verify
description: Bank-to-bank migration validation via the verify-only tests/migration suite — run, evidence, sign-off checklist
argument-hint: "[env-file e.g. config/environments/phase1_hop2_stage_int.yaml]"
allowed-tools:
  - read
  - grep
  - glob
  - exec
---

BANK MIGRATION VALIDATION (source → target, per hop)

The `tests/migration/` suite validates a loaded target AS-IS — read-only on
both sides, no loader. The mapping workbook is the source of truth for what
migrates. Phase-1 reference envs: `config/environments/phase1_hop*.yaml`
(CSV → `wdr_uat_active_stage.signature` → `wdr_uat_integration.wdr_int_core`
→ `wdr_uat_consumption.wdr_cl_fds_core`).

## 1. Setup / environment

- Demo pipeline: `python scripts/generate_phase1_workbooks.py` +
  `generate_phase1_csv.py`, then per hop `load_phase2_hop.py --hop N` with
  `provision_phase2.py --views stage|il` between loads. Full chain in
  docs/RUNBOOK.md.
- Real UAT: point the phase1_* env yamls at client hosts, set
  `${RECON_*}` env vars, and `run.snapshot_id` (agreed frozen migration
  cut) — the library refuses real-target runs without enforced snapshots.
- Verify the env YAML: source creds are a READ-ONLY role; the verifier
  never writes to either side.

## 2. Scope & mapping

- The workbook `Tables` sheet = declared scope. Enabled rows must cover
  every in-scope entity; review `unmapped_source_tables` /
  `unmapped_target_tables` in the evidence — an unmapped table is NOT
  implicitly certified.
- `Where Clause` = scoped migration; leaked out-of-scope rows surface as
  `extra_in_target`.
- Run all enabled mappings (default) or scope with
  `-v MAPPING_ID:<id>` — but a release gate needs every enabled mapping.

## 3. Execute

```
robot -d results/phase1/hopN -v MIGRATION_ENV:<env>.yaml \
  -v MAPPING_WORKBOOK:<wb>.xlsx tests/migration      # all stages
robot -d results/n tests/migration/90_negative_path.robot   # detection proof
```

Stages per mapping: `mapping` (compile) → `source` (columns/counts) → `dq`
(contract rules + map codes + key uniqueness) → `schema` (target DDL vs
workbook) → `recon` (exact counts, missing/extra, per-column diffs) →
`transform` → `controls` (trailer) → `relations` (FK orphans). `e2e` runs
all; `negative` proves detection on streamed copies.

## 4. Evidence & sign-off

- `run_summary.json` — `verdict`, `coverage` (must be COMPLETE for
  sign-off), `meta` (environment, pipeline_id, hop, snapshot_id, workbook
  sha256, git commit, executor).
- `migration_summary.xlsx` — Run Info / Stage Results / Metadata /
  Diffs & Violations / Insights sheets (audit workbook).
- `migration_report.md` — readable QA report.
- `production_signoff` is ALWAYS false — a green run is not sign-off;
  real-data + snapshot validation is required.
- Sign-off needs: every enabled mapping, every stage PASS, zero
  missing/extra/diffs, zero failing DQ rules. Any mismatch: report mapping,
  key, column, expected vs actual — never "data looks fine".
- Evidence is sensitive (contains compared data): git-ignored, share only
  through approved channels.

## 5. Escalate, don't improvise

Ask before: running against a production-like endpoint, any write to a bank
database, extending scope beyond the workbook, or handling data that might
be real customer data. Unenforceable checks must stay flagged — never
assert them validated.
