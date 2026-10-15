# Workflow — Banking Migration Verification Framework

End-to-end operator guide: how the project runs, what each suite proves, and
where the evidence lands. Complements `RUNBOOK.md` (setup detail) and
`EXCEL_MAPPING.md` (workbook authoring).

## Big picture

```
┌─────────────────────────────────────────────────────────────────┐
│  SOURCE BANK (read-only)                                        │
│  demo: webster schema ──┐   demo2: bank1 schema (ent_*)         │
│  real: ${BANK_*} env refs │                                     │
│                         ▼                                       │
│         PostgresSource / MysqlSource / OracleSource / ...       │
│         (snapshot: exported → pg_export_snapshot pinning,       │
│          or snapshot_column watermark for other engines)        │
│                         │                                       │
│      ┌──────────────────┼──────────────────┐                    │
│      ▼                  ▼                  ▼                    │
│  Mapping workbook   Transform engine   Controls engine          │
│  (Tables/FieldMap/  (safe expressions) (counts/sums/trailer)     │
│   Mapping/Controls/                                             │
│   Relations)                                                    │
│      │                                                          │
│      ▼                                                          │
│  Comparison engine (external canonical sort → exact counts      │
│  missing / extra / column diffs)                                │
│      ▲                                                          │
│      │        TARGET BANK (read-only)                           │
│      └──── SnowflakeTarget (or emulated fakesnow for demo)      │
│                         │                                       │
│                         ▼                                       │
│   Evidence → run_summary.json / migration_summary.xlsx /        │
│              migration_report.md / Robot report+log             │
└─────────────────────────────────────────────────────────────────┘
```

Two demo migrations ship with the repo:

| Scenario | Source | Target | Env | Workbook |
|---|---|---|---|---|
| Webster→Santander | `webster` schema (10k customers) | emulated `SANTANDER` | `webster_santander.yaml` | `bank_mapping.xlsx` |
| Bank1→MSS | `bank1` schema (ent_* enterprise) | emulated `MSS_CORE` | `bank1_to_mss.yaml` | `bank1_to_mss.xlsx` |

## Prerequisites (once)

1. Docker Desktop running with `recon-db` (Postgres 16, port 5432).
   Role DDL: `.github/workflows/reconciliation.yml` "Provision recon database".
2. `.venv` with `requirements.txt` installed — or the global Python 3.11
   (both work; `.venv/Scripts/robot` is canonical).
3. `.env` with `RECON_RO_USER`, `RECON_RO_PASSWORD`, `RECON_RW_USER`,
   `RECON_RW_PASSWORD` (git-ignored).

## Daily workflow — three phases

### Phase 1 — Seed the source bank(s)

```bash
export RECON_ADMIN_USER=postgres RECON_ADMIN_PASSWORD=postgres_local

# Webster (10k customers, scales with --rows)
.venv/Scripts/python scripts/generate_bank_source.py --rows 10000 --confirm-synthetic

# Bank-1 enterprise ent_* schema
.venv/Scripts/python scripts/generate_bank1_source.py --rows 500 --confirm-synthetic
```

Seeder safety: refuses to overwrite an existing schema — drop first:

```bash
docker exec recon-db psql -U postgres -d recon \
  -c "DROP SCHEMA webster CASCADE; DROP SCHEMA bank1 CASCADE;"
```

Each run also regenerates the certified bank trailer
(`data/trailer/*_trailer.csv`) that the controls stage validates against.

### Phase 2 — Load the target bank(s) (demo only)

Verification is read-only; in a real migration the bank's ETL loads the
target. For demos, `load_migration_target.py` plays ETL — it refuses
non-emulated targets:

```bash
.venv/Scripts/python scripts/load_migration_target.py          # webster -> SANTANDER
.venv/Scripts/python scripts/load_migration_target.py \
  --env config/environments/bank1_to_mss.yaml \
  --workbook data/mapping/bank1_to_mss.xlsx                    # bank1 -> MSS_CORE
```

To start targets from zero: delete `results/fakesnow*` before loading.

### Phase 3 — Run the verification suites

```bash
# Webster -> Santander
.venv/Scripts/robot -d results/migration tests/migration

# Bank-1 -> MSS (same suites, different env + workbook)
.venv/Scripts/robot -d results/mss \
  -v MIGRATION_ENV:config/environments/bank1_to_mss.yaml \
  -v MAPPING_WORKBOOK:data/mapping/bank1_to_mss.xlsx \
  tests/migration

# Pilot suites (CSV -> postgres feeds + business rules)
.venv/Scripts/robot -d results/pilot tests/pilot

# Unit tests
.venv/Scripts/python -m unittest discover -s tests/pilot -p "test_*.py"
.venv/Scripts/python -m unittest discover -s tests/migration -p "test_*.py"
```

## What each migration suite proves

| Suite | Checks |
|---|---|
| `01_mapping_and_source` | Workbook compiles; mapped tables/columns exist; unmapped tables listed |
| `02_source_data_quality` | Contract types/nullability/unique keys + row_filter scope |
| `03_target_schema` | Column names, order, types, scale, nullability vs workbook |
| `04_record_comparison` | **Exact counts** — missing / extra keys / per-column diffs |
| `05_transformations` | Recomputed transforms vs stored target values |
| `06_pipeline` | End-to-end gate + evidence coverage must be `COMPLETE` |
| `07_controls` | Counts/sums/min/max + certified bank-trailer file match |
| `08_relations` | FK orphans across mappings, **source and target** sides |
| `90_negative_path` | Injects missing/tamper/duplicate/extra on streamed copies — detection proof |

## Reading the evidence

| Artifact | Use |
|---|---|
| `results/*/migration_report.md` | Human-readable verdict per mapping |
| `results/*/migration_summary.xlsx` | Run Info + Stage Results + Diffs + Controls + Insights — the audit packet |
| `results/*/run_summary.json` | Same data, machine-readable (`results[]` rows keyed `mapping`/`stage`) |
| `results/*/report.html`, `log.html` | Robot PASS/FAIL + keyword-level detail |
| `results/pilot/report.html` | Pilot gate |

Key fields: `verdict` (PASS only when every executed check passed),
`coverage` (must be `COMPLETE`), `meta.source_snapshot` (live
`pg_export_snapshot` id = cutover binding), `production_signoff`
(always `false` — never auto-granted).

In `Stage Results`, counts fill progressively: `mapping`/`source` rows are
source-side only; `recon` rows carry Source/Target Rows + Missing/Extra/
Column Diffs and a self-describing detail like
`10000/10000 rows match; 0 missing / 0 extra / 0 column diffs`.

## Failure triage

| Symptom | Look at | Typical cause |
|---|---|---|
| `configuration/setup: ERROR` | `run_summary.json` setup row detail | Docker down / Postgres unreachable / bad env ref |
| All target stages `ERROR` | first target-stage row `detail` | Missing driver (`fakesnow`, `snowflake-connector`) or target DB down |
| `unmapped_source_tables` non-empty | source stage rows | Source table without workbook mapping — declare or `fail_on_unmapped` |
| `missing`/`extra` > 0 | Diffs sheet + recon row | Rows lost/duplicated by the loader |
| `column diffs` > 0 | Diffs sheet (PII-redacted samples) | Wrong transform or drifted column |
| `control_mismatches` | controls row + trailer file | Financial totals don't reconcile or trailer is stale |
| `orphans` in relations | relations row — source vs target side | Source data defect vs migration integrity break |
| `post_snapshot_arrivals` > 0 | recon row | Rows landed after the pinned cut — delta migration needed |

## Full rebuild (what "fresh run" means)

```bash
rm -rf results
docker exec recon-db psql -U postgres -d recon \
  -c "DROP SCHEMA IF EXISTS webster CASCADE; DROP SCHEMA IF EXISTS bank1 CASCADE;"
# then Phase 1 -> 2 -> 3 above
```

## Onboarding a real bank migration

1. DBA grants a **SELECT-only** role on source and target.
2. Create `config/environments/<name>.yaml` — connection literals only,
   credentials as `${ENV_VAR}` refs.
3. Business fills the workbook: Tables → FieldMap → Mapping → Relations →
   Controls. Columns with no source stay unmapped (they surface as gaps).
4. Provide the certified trailer file and set `run.snapshot_id` to the
   agreed migration cut (+ `snapshot_column` for non-Postgres sources).
5. Run the suite — verify-only, no writes to either bank.
6. Iterate the workbook until `verdict: PASS` + `coverage: COMPLETE`,
   then route `migration_summary.xlsx` + `migration_report.md` to the
   business/finance/risk/technology sign-off owners.

Details: `docs/EXCEL_MAPPING.md` (workbook authoring), `docs/ADAPTERS.md`
(other engines), `docs/RUNBOOK.md` (setup/troubleshooting).
