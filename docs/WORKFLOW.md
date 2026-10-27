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
        │  via v_dim_customer_src    │   wdr_int_core.dim_*          │
        │       (CUP00301+CUP01201   │                              │
        │        +DIM_REFERENCE join)│ wdr_uat_consumption          │
        │                            │   wdr_cl_fds_core            │
        └── hop 3 via                │     .cl_party_snpsht         │
            v_party_snpsht_src view  └──────────────────────────────┘
                     │
                     ▼
        tests/migration — same 9-stage verify-only suite per hop
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
| `recon_rw` | `scripts/load_phase2_hop.py`, `provision_phase2.py` | schema + data writes on the `wdr_uat_*` demo DBs |
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
.venv/Scripts/python scripts/load_phase2_hop.py --hop 1
.venv/Scripts/python scripts/provision_phase2.py --views stage
.venv/Scripts/python scripts/load_phase2_hop.py --hop 2
.venv/Scripts/python scripts/provision_phase2.py --views int
.venv/Scripts/python scripts/load_phase2_hop.py --hop 3
```

Views between loads: hop-2 reads `signature.v_dim_customer_src` (the
spec's DIM_REFERENCE lookup); hop-3 reads `wdr_int_core.v_party_snpsht_src`
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

| Suite | Stage | Proves |
|---|---|---|
| 01 | mapping, source | workbook compiles; source metadata matches; unmapped inputs surfaced |
| 02 | dq | source DQ rules (nulls, lengths, patterns) |
| 03 | schema | target columns/types/nullability match the workbook |
| 04 | recon | exact row sets — missing/extra/duplicates/diffs, bounded samples |
| 05 | transform | every declared transform recomputes on target rows |
| 06 | pipeline | all stages PASS in one pass; evidence `coverage == COMPLETE` |
| 07 | controls | certified trailer totals (`name,group,value`) vs actuals |
| 08 | relations | FK orphans on both sides of each declared/derived relation |
| 10 | rules | opt-in rules workbook passes; env-scope guard skips wrong-hop runs |
| 90 | negative | tampered streamed copies are caught — DBs untouched |

## Evidence map

| Artifact | Audience |
|---|---|
| `results/phase1/hopN/migration_report.md` | business/finance sign-off |
| `results/phase1/hopN/migration_summary.xlsx` | audit — Run Info has timestamp, snapshot id, commit, executor, verdict |
| `results/phase1/hopN/run_summary.json` | automation/CI — per-stage counts, `pipeline_id`, `hop` |
| `results/phase1/hopN/report.html`, `log.html` | release gate / debugging |
| `results/phase1/hopN/output.xml` | CI parsing |

## Failure triage

| Symptom | Where to look | Typical cause |
|---|---|---|
| Workbook compile error | hopN `log.html` → suite 01 | spec gap in Phase1.xlsx (fail-closed is correct) |
| `extra: N` on recon | `run_summary.json` stage `recon` | source rows never landed (loader or extract) |
| `missing: N` | same | target holds rows outside the extract |
| Control FAIL | suite 07 evidence | trailer vs loaded data drift |
| FK orphans | suite 08 evidence | child rows loaded without parents |
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
