# Workflow — Phase-1 Banking Data Migration Validation

Operator flow for the three-hop pipeline in `data/trailer/Phase1.xlsx`.
Everything here is reproducible from scratch; see `docs/RUNBOOK.md` for
setup detail and `docs/EXCEL_MAPPING.md` for workbook authoring.

## Architecture

```
CSV extracts (one file/table)        PostgreSQL 16 (local recon-db)
data/phase1/csv/*.csv                ┌──────────────────────────────┐
        │                            │ wdr_uat_active_stage         │
        │  hop 1                     │   signature.cup00301         │
        ▼                            │   signature.cup01201         │
   signature (landing) ─── hop 2 ──► │ wdr_uat_integration          │
        │  via v_dim_customer_src    │   wdr_il_core.dim_*          │
        │       (CUP00301+CUP01201   │                              │
        │        +DIM_REFERENCE join)│ wdr_uat_consumption          │
        │                            │   wdr_cl_fds_core            │
        └── hop 3 via                │     .cl_party_snpsht         │
            v_party_snpsht_src view  └──────────────────────────────┘
                     │
                     ▼
        tests/migration — same verify-only suites per hop
        (01_end_to_end_migration_testing = the whole chain; 90_negative_path = proof)
                     │
                     ▼
        results/phase1/hopN/ — evidence (html/md/xlsx/json)
```

Each hop compiles its workbook into a contract at runtime: schema →
source reads → canonical sort → chunked recon → transform recompute →
controls/trailer → relations → evidence. Verification is `recon_ro`
(SELECT-only); loading is `recon_rw` out-of-band — same split as a real
engagement where the bank's ETL owns target writes.

## Roles

| Role | Used by | Rights |
|---|---|---|
| `recon_ro` | `tests/migration` suites, all source/target reads | SELECT only |
| `recon_rw` | `scripts/load_phase1_hop.py`, `provision_phase1.py` | schema + data writes on the `wdr_uat_*` demo DBs |
| `postgres` | one-time DB/role provisioning | admin — CI/local setup only |

## Daily workflow

### 1. Regenerate artifacts (spec → workbooks, deterministic fixtures)

```bash
.venv/Scripts/python scripts/generate_phase1_workbooks.py   # 3 workbooks from Phase1.xlsx spec
.venv/Scripts/python scripts/generate_phase1_csv.py         # csvs + 7 negative cases + 3 trailers
```

Fixtures are synthetic and seeded — 200 customers / 160 legal entities /
6 reference rows (3 keyed by `attribute_map_cd` for the hop-3
`customer_category` override), same every run. `data/phase1/` and
`data/trailer/*.csv` are committed synthetic fixtures — regenerated via
the script, never hand-edited.

### 2. Load targets (the "ETL" — RW role)

```bash
.venv/Scripts/python scripts/load_phase1_hop.py --hop 1
.venv/Scripts/python scripts/provision_phase1.py --views stage
.venv/Scripts/python scripts/load_phase1_hop.py --hop 2
.venv/Scripts/python scripts/provision_phase1.py --views il
.venv/Scripts/python scripts/load_phase1_hop.py --hop 3
```

Views between loads: hop-2 reads `signature.v_dim_customer_src` (the
spec's DIM_REFERENCE lookup); hop-3 reads `wdr_il_core.v_party_snpsht_src`
(customer ⨝ address, LEFT JOIN `dim_reference` on
`attribute_map_cd = naics_cd` — feeds the `customer_category` override
arm). Both are `CREATE OR REPLACE`, read-only consumers.

### 3. Verify each hop (RO role — the release gate)

```bash
.venv/Scripts/robot -d results/phase1/hop1 \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
# same pattern for hop2 / hop3
```

### 4. Negative-path proof

```bash
.venv/Scripts/robot -d results/phase1/hop1_negative \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage_negative.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
# expected FAIL: recon extra:5, trailer controls fail, 4 FK orphans
```

CI (`.github/workflows/reconciliation.yml`) runs all of the above and
treats a *passing* negative run as an error — detection is the feature.

## What each suite proves (per hop)

`01_end_to_end_migration_testing.robot` — one suite, one input workbook, one
consolidated report. Five ordered lifecycle tests:

| Test (end-to-end phase) | Stages | Proves |
|---|---|---|
| Requirements Intake And Mapping Compile | mapping | workbook compiles; unmapped inputs surfaced |
| Source And Schema Conformance | source, dq, schema | source metadata + DQ rules; target columns/types/nullability match the workbook |
| Transformation And Record Reconciliation | recon, transform | exact row sets (missing/extra/diffs); every declared transform recomputes |
| Business Validation … | controls, relations, profile, rules | certified trailer totals; FK orphans; column profiles; `Rules`-sheet business rules (env-scope guard skips wrong-hop runs) |
| Lifecycle Evidence Is Complete And Passing | — | verdict PASS, coverage COMPLETE; `end_to_end_summary.xlsx` + `end_to_end_report.md` written |

`90_negative_path.robot` — tampered streamed copies are caught (DBs
untouched).

## Evidence map

| Artifact | Audience |
|---|---|
| `results/phase1/hopN/end_to_end_summary.xlsx`, `end_to_end_report.md` | consolidated lifecycle report — reviewer sheets (stages, controls, rules w/ plain-English, RTM, scorecard) + QA diagnostics (Data Diffs, Queries, Column Profile, Metadata, Insights) |
| `results/phase1/hopN/migration_report.md` | business/finance sign-off |
| `results/phase1/hopN/migration_summary.xlsx` | audit — Run Info has timestamp, snapshot id, commit, executor, verdict |
| `results/phase1/hopN/run_summary.json` | automation/CI — per-stage counts, `pipeline_id`, `hop` |
| `results/phase1/hopN/report.html`, `log.html` | release gate / debugging |
| `results/phase1/hopN/output.xml` | CI parsing |

## Failure triage

| Symptom | Where to look | Typical cause |
|---|---|---|
| Workbook compile error | hopN `log.html` → test 1 (mapping) | spec gap in Phase1.xlsx (fail-closed is correct) |
| `extra: N` on recon | `run_summary.json` stage `recon` | source rows never landed (loader or extract) |
| `missing: N` | same | target holds rows outside the extract |
| Control FAIL | `end_to_end_summary.xlsx` Controls sheet | trailer vs loaded data drift |
| FK orphans | `end_to_end_summary.xlsx` Stages sheet (`relations` rows) | child rows loaded without parents |
| `password authentication failed` | — | wrong role/credential; verification is RO-only |

## Full rebuild (clean slate)

```bash
rm -rf results/phase1 data/phase1 data/trailer/phase1_*.csv
# databases persist; loaders truncate-and-reload their own tables
# (drop the wdr_uat_* schemas to reset fully, then re-apply the CI
#  provisioning block, then the chain above)
```

## Real UAT onboarding

1. Client delivers the real spec workbook, extracts, and trailer —
   same sheet formats, no code changes.
2. Point `config/environments/phase1_*.yaml` at client hosts; credentials
   stay in `.env`/env vars (`${VAR}` refs only).
3. Set `run.snapshot_id` to the agreed frozen cut; drop `allow_qa_load`.
4. Regenerate or author the three mapping workbooks; unresolved spec
   items fail closed at compile — that IS the spec-gap report.
5. Run the three hops; evidence carries client `pipeline_id`/`hop`.
6. `production_signoff` is never auto-granted — evidence goes to the
   sign-off authority.
