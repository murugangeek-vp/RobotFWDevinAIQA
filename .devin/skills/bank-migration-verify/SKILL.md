---
name: bank-migration-verify
description: Bank-to-bank migration validation via the verify-only tests/migration suite — run, evidence, sign-off checklist
argument-hint: "[env-file e.g. config/environments/webster_santander.yaml]"
allowed-tools:
  - read
  - grep
  - glob
  - exec
---

BANK MIGRATION VALIDATION (source bank → target bank)

The `tests/migration/` suite validates a loaded target AS-IS — read-only on
both sides, no loader. The mapping workbook is the source of truth for what
migrates. Reference env: `config/environments/webster_santander.yaml`
(Webster Postgres → Santander Snowflake, `fake: true` emulated target).

## 1. Setup / environment

- Emulated demo: seed source + load target —
  `python scripts/generate_bank_source.py --rows N --confirm-synthetic` then
  `python scripts/load_migration_target.py`.
- Real account: `config/environments/webster_santander_live.yaml` — all
  `${SNOWFLAKE_*}` / `${SOURCE_PG_*}` / `RECON_RO_*` env vars set, and
  `run.snapshot_id` (agreed frozen migration cut) — the library refuses
  real-target runs without it.
- Verify the env YAML: source creds are a READ-ONLY role; the verifier never
  writes to either bank.

## 2. Scope & mapping

- `data/mapping/bank_mapping.xlsx` `Tables` sheet = declared scope. Enabled
  rows must cover every in-scope entity; review `unmapped_source_tables` /
  `unmapped_target_tables` in the evidence — an unmapped table is NOT
  implicitly certified.
- `Where Clause` = scoped migration (e.g. active accounts only); leaked
  out-of-scope rows surface as `extra_in_target`.
- Run all enabled mappings (default) or scope with
  `-v MAPPING_ID:<id>` — but a release gate needs every enabled mapping.

## 3. Execute

```
robot -d results/migration tests/migration          # all 7 stages
robot -d results/m1 -v MAPPING_ID:account_migration tests/migration
robot -d results/n tests/migration/90_negative_path.robot   # detection proof
```

Stages per mapping: `mapping` (compile) → `source` (columns/counts) → `dq`
(contract rules + map codes + key uniqueness) → `schema` (target DDL vs
workbook) → `recon` (exact counts, missing/extra, per-column diffs) →
`transform`. `e2e` runs all; `negative` proves detection on streamed copies.

## 4. Evidence & sign-off

- `run_summary.json` — `verdict`, `coverage` (must be COMPLETE for sign-off),
  `meta` (environment, snapshot_id, workbook sha256, git commit, executor).
- `migration_summary.xlsx` — Run Info / Stage Results / Metadata /
  Diffs & Violations / Insights sheets (audit workbook).
- `migration_report.md` — readable QA report.
- `production_signoff` is ALWAYS false — a green emulated run is not
  sign-off; real-account + snapshot validation is required.
- Sign-off needs: every enabled mapping, every stage PASS, zero
  missing/extra/diffs, zero failing DQ rules. Any mismatch: report mapping,
  key, column, expected vs actual — never "data looks fine".
- Evidence is sensitive (contains compared data): git-ignored, share only
  through approved channels.

## 5. Escalate, don't improvise

Ask before: running against a production-like endpoint, any write to a bank
database, extending scope beyond the workbook, or handling data that might be
real customer data. Known emulator gaps (VARCHAR length "not validated",
constraint semantics) must stay flagged — never assert them validated.
