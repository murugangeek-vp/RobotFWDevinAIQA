# Runbook — Phase-1 Banking Data Migration Validation

End-to-end validation of a three-hop migration pipeline defined by the
client spec `data/trailer/Phase1.xlsx`:

| Hop | Source | Target (PostgreSQL) | Env / workbook |
|---|---|---|---|
| 1 | CSV extracts (`source.dir`, one file per table) | `wdr_uat_active_stage.signature` | `phase1_hop1_csv_stage.yaml` / `phase1_hop1_csv_to_stage.xlsx` |
| 2 | `signature` tables + `v_dim_customer_src` view | `wdr_uat_integration.wdr_il_core` | `phase1_hop2_stage_il.yaml` / `phase1_hop2_stage_to_il.xlsx` |
| 3 | `wdr_il_core.v_party_snpsht_src` view | `wdr_uat_consumption.wdr_cl_fds_core` | `phase1_hop3_il_consumption.yaml` / `phase1_hop3_il_to_consumption.xlsx` |

The same `tests/migration` suites run per hop — all 9 stages (mapping,
source metadata, DQ, schema, recon, transforms, pipeline, controls,
relations) plus the `90_` negative-path proof. `pipeline_id:
phase1-uat-demo` in the evidence metadata links the three runs into one
auditable chain.

## Prereqs

- Python 3.11+ (3.12 preferred), `uv` or `pip`
- A reachable PostgreSQL 16. Local Docker:

  ```bash
  docker run -d --name recon-db -e POSTGRES_PASSWORD=postgres_local -p 5432:5432 postgres:16-alpine
  ```

- The three `wdr_uat_*` databases, schemas, and least-privilege roles.
  Full provisioning DDL is in `.github/workflows/reconciliation.yml` (the
  "Provision phase-1 databases" step) — reuse it for any fresh environment.
  Locally it was applied once via `psql`; the read-only join views are
  (re)created by `scripts/provision_phase2.py` at each stage of the load.

## Setup (< 5 min)

```bash
uv venv .venv && uv pip install --python .venv/Scripts/python -r requirements.txt
# or: python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

## Credentials (never committed)

Copy `.env.example` to `.env` (git-ignored) and fill in the passwords — the
library loads it automatically on suite setup:

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Variables already set in the environment (e.g. CI secrets) override `.env`:

```bash
export RECON_RO_USER=recon_ro   RECON_RO_PASSWORD=...
export RECON_RW_USER=recon_rw   RECON_RW_PASSWORD=...
```

Windows PowerShell: `$env:RECON_RO_USER="recon_ro"` etc.

Roles: `recon_ro` is SELECT-only and serves all verification; `recon_rw`
is the fixture loader only. The suites never open a write connection —
`allow_qa_load: true` exists on the demo envs solely so the *loader script*
can populate the synthetic targets out-of-band, standing in for the bank's
ETL.

## Full chain (generate → load → verify)

```bash
# 1. Regenerate artifacts (workbooks from spec, deterministic CSVs, trailers)
.venv/Scripts/python scripts/generate_phase1_workbooks.py
.venv/Scripts/python scripts/generate_phase1_csv.py

# 2. Load each hop's target (RW role — the "ETL" for the demo)
.venv/Scripts/python scripts/load_phase2_hop.py --hop 1     # CSV -> signature
.venv/Scripts/python scripts/provision_phase2.py --views stage   # join view for hop 2
.venv/Scripts/python scripts/load_phase2_hop.py --hop 2     # signature -> wdr_il_core
.venv/Scripts/python scripts/provision_phase2.py --views il      # join view for hop 3
.venv/Scripts/python scripts/load_phase2_hop.py --hop 3     # wdr_il_core -> wdr_cl_fds_core

# 3. Verify each hop (RO role only)
.venv/Scripts/robot -d results/phase1/hop1 \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
.venv/Scripts/robot -d results/phase1/hop2 \
  -v MIGRATION_ENV:config/environments/phase1_hop2_stage_il.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop2_stage_to_il.xlsx tests/migration
.venv/Scripts/robot -d results/phase1/hop3 \
  -v MIGRATION_ENV:config/environments/phase1_hop3_il_consumption.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop3_il_to_consumption.xlsx tests/migration
```

Evidence per hop lands in `results/phase1/hopN/`: `report.html`,
`log.html`, `migration_report.md`, `migration_summary.xlsx`,
`run_summary.json` (with `pipeline_id`, `hop`, snapshot id, git commit,
verdict; `production_signoff` is never granted by a run).

## Snapshot / cutover semantics

- Hop 1 (CSV): `source.snapshot_column: load_date` applies the declared
  watermark (`load_date <= run.snapshot_id`) as a real filter on the CSV
  frame — the file's extract date acts as the frozen cut.
- Hops 2–3 (Postgres): `run.snapshot_id` is a real `pg_export_snapshot`
  id captured at load time — the verifier reads exactly the frozen
  transaction state, provably identical to what the loader saw.
- A non-fake target **requires** enforced snapshots — the library refuses
  to verify live drift on production-like connections.

## Negative-path demo (expected FAIL)

`phase1_hop1_csv_stage_negative.yaml` points `source.dir` at
`data/phase2/negative/missing_rows/` — the same suite against corrupted
extracts must fail:

```bash
.venv/Scripts/robot -d results/phase1/hop1_negative \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage_negative.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
```

Observed: recon `src 195 / tgt 200 / extra: 5` (exact counts), trailer
controls FAIL vs certified totals, relations flag 4 FK orphans. Other
corruption cases live under `data/phase2/negative/` — point `source.dir`
at any of them.

## Spec-gap resolutions (documented in each workbook README)

- `CUSIC` maps to two target columns — split resolved: `APPL_KEY='CBS'`
  (authoritative), `CUSIC` feeds `NAICS_CD` only.
- `DIM_REFERENCE` is a lookup, not a table map — resolved via the
  read-only `signature.v_dim_customer_src` join view.
- CASE arms without ELSE get `ELSE 'UNSPECIFIED'` (declared, not silent).
- `PARTY_TYPE` "complex logic" is pending spec — emitted as the visible
  sentinel `literal('PENDING_SPEC')`, never silently dropped.
- `LOAD_DATE` is an auditable run parameter (`run.load_date` →
  `__load_date` pseudo-column in transforms), not a source fetch.

## Live (read-only) mode — `phase1_*_live.yaml`

Production-shape verification: no `allow_qa_load`, no loader credentials,
`RECON_RO_*` only, and `run.snapshot_id` is mandatory (a non-emulated target
refuses to run without an enforceable cut). All connection values come from
env vars:

```bash
export RECON_RO_USER=recon_ro  RECON_RO_PASSWORD=...
export MIGRATION_SNAPSHOT_ID=2026-10-15        # the agreed frozen cut
export PG_SSLMODE=verify-full                  # disable for local rehearsal
export STAGE_PG_HOST=...  IL_PG_HOST=...  CONSUMPTION_PG_HOST=...
export LANDING_PG_HOST=...                     # hop-1 target
# prod TLS: add `sslrootcert: ${PG_CA_FILE}` under each postgres block and
# export PG_CA_FILE=/path/to/ca-chain.pem

robot -d results/phase1/hop1 \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage_live.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
```

Local rehearsal (docker DBs, no TLS): `PG_SSLMODE=disable`, hosts `localhost`,
`MIGRATION_SNAPSHOT_ID=2026-10-15`. DB hops enforce the cut via a real
`pg_export_snapshot()` held on a `readonly=True` session — evidence records
the actual snapshot id (e.g. `00000009-00000194-1`).

## Onboarding real UAT data

Swap the env yamls to the client hosts/credentials, set `run.snapshot_id`
to the agreed frozen cut, drop `allow_qa_load`, replace the workbooks with
the client-authored mapping (same sheet format), and point `source.dir` /
`control_file` at the real extracts and certified trailer. No code changes
— that is the point of the design.

## Common failures

| Symptom | Likely layer |
|---|---|
| Mapping compile errors in 01 | workbook (fail-closed spec gaps surface here) |
| `unmapped_source_tables` insights in 01 | extra CSV files or scope leak |
| Rule failures in 02 | source data quality |
| `table ... does not exist` / type mismatch in 03 | target schema drift |
| `missing_in_target` / `extra_in_target` in 04 | load layer / incomplete extract |
| `Get Transform Diffs` non-empty in 05 | transform logic mismatch |
| Control mismatches in 07 | trailer totals vs loaded data |
| Orphans in 08 | referential integrity broken by load |
| `password authentication failed` | wrong role — verification must run as `recon_ro`, loader as `recon_rw` |
| `snapshot required` on a real target | `run.snapshot_id` unset — mandatory for non-demo runs |
