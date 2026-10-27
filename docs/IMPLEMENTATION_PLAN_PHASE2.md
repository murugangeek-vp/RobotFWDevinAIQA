# Phase 2 Implementation Plan — Multi-Hop CSV→Landing→Integration→Target Verification

## Requirement

Build Robot Framework coverage for the full warehouse migration chain:

```
CSV files (one folder per table)
   └─ Step 1: CSV data validation (all layers)
        └─ Step 2: CSV → Landing DB (WDRUAT_ACTIVE_STAGE_DB) — all layers
             └─ Step 3: Landing → Integration (WDRUAT_INTEGRATION_DB) — all layers
                  └─ Step 4: Integration → Target (WDRUAT_CONSUMPTION_DB) — all layers
```

All hops are PostgreSQL. Spec source: `data/trailer/Phase1.xlsx` —
`SIGNATURE.CUP00301` / `CUP01201` / `DIM_REFERENCE` → `WDR_IL_CORE.DIM_CUSTOMER`,
`DIM_CUSTOMER_ADDRESS` → `WDR_CL_FDS_CORE.CL_PARTY_SNPSHT`, with real bank
transforms: `LPAD`, `TRIM`, `CASE WHEN`, `COALESCE`, `REGEXP_LIKE`, string
concat (`'CBS'||'CUST'||...`), hardcoded values, `LOAD_DATE` system fields.

## What the framework already gives us (reuse)

- `tests/migration/` suites are env-agnostic: every stage (mapping/source/dq/
  schema/recon/transform/controls/relations/negative) runs against whatever
  env+workbook is passed via `-v MIGRATION_ENV:` `-v MAPPING_WORKBOOK:`.
- `CsvSource` adapter + `PostgresSource`/`PostgresTarget` (+ mysql/oracle/mssql).
- Safe transform whitelist (`_SAFE_FUNCS`: upper/lower/trim/substr/round/
  date_fmt/ifnull/casewhen/band/map/prefix/suffix).
- Controls/trailer certification, FK relations, PII redaction, exported-snapshot
  pinning, fail-closed coverage gate.

## Design decision: one hop = one env + one workbook

Each hop is an independent source→target verification — the existing suites
run **unchanged**, just pointed at different env/workbook pairs:

| Hop | Env yaml | Workbook | Source adapter | Target adapter |
|---|---|---|---|---|
| 1 CSV→Landing | `phase2_csv_to_stage.yaml` | `p1_csv_to_stage.xlsx` | `csv` (folder) | `postgres` WDRUAT_ACTIVE_STAGE_DB |
| 2 Landing→Integration | `phase2_stage_to_int.yaml` | `p1_stage_to_int.xlsx` | `postgres` stage | `postgres` WDRUAT_INTEGRATION_DB |
| 3 Integration→Target | `phase2_int_to_consumption.yaml` | `p1_int_to_consumption.xlsx` | `postgres` WDR_IL_CORE | `postgres` WDR_CL_FDS_CORE |

New concept needed: **hop-chain evidence** — the three runs share a
`pipeline_id`/`hop` tag in `run_summary.json` meta so the full chain is
auditable end-to-end.

## Workstreams

### P2-1 — Postgres multi-database demo infra
- Extend recon-db provisioning: create `WDRUAT_ACTIVE_STAGE_DB`,
  `WDRUAT_INTEGRATION_DB`, `WDRUAT_CONSUMPTION_DB` databases + schemas
  (`SIGNATURE`, `WDR_IL_CORE`, `WDR_CL_FDS_CORE`), `recon_ro`/`recon_rw` grants.
- Update `.env` whitelist + `.env.example` + RUNBOOK + CI provisioning step.

### P2-2 — SQL-expression transform layer (the big gap)
Workbook transforms are declared as SQL snippets; the engine evaluates safe
expressions only. Two-part implementation:
- **Extend `_SAFE_FUNCS`** with the small set this spec needs:
  `lpad(col, n, '0')`, `concat(a, b, ...)`, `coalesce`-style `ifnull`,
  `casewhen` already covers `CASE WHEN x='N' THEN ...` equality forms.
- **Workbook compiler translation**: FieldMap rows keep engine expressions;
  a `sql_to_transform` helper (sqlglot, already a dep) parses the Excel's SQL
  snippets into engine expressions where a safe equivalent exists, and
  **fails closed** listing rows that need manual mapping (e.g. REGEXP_LIKE
  until a `regexp` safe func is added deliberately).
- `LOAD_DATE`/`CURRENT_DATE` system columns: supported via a declared
  `run.load_date` run parameter injected by config (deterministic, auditable)
  — never wall-clock at compare time.

### P2-3 — Phase-1 mapping workbooks (3 hops)
- `data/mapping/p1_csv_to_stage.xlsx` — CSV file→`SIGNATURE` tables
  (type coercion, header validation, row counts, rejected-rows controls).
- `data/mapping/p1_stage_to_int.xlsx` — the Excel's hop-1 rows as FieldMap
  (CUP00301→DIM_CUSTOMER 13 fields, CUP00301→DIM_CUSTOMER_ADDRESS,
  CUP01201/DIM_REFERENCE joins → multi-source mapping rows needed —
  `DIM_REFERENCE` lookup is a join; mark join-supported mapping rows
  `Multi-Source` or extend FieldMap with a Source Table column).
- `data/mapping/p1_int_to_consumption.xlsx` — the Excel's hop-2 rows.
- Keys: `INVOLVED_PARTY_KEY` (concat-derived), `PARTY_IDENTIFIER` on target;
  Controls: row counts per table, `TAX_ID_NUMBER` null_counts,
  BUSINESS_DOMAIN group counts; Relations: customer↔address,
  party snapshot ↔ dimension.

### P2-4 — CSV fixture generator + hop loaders
- `scripts/generate_phase1_csv.py` — deterministic synthetic CSVs per table
  (CUP00301/CUP01201/DIM_REFERENCE + negative files: malformed rows, dup keys,
  bad codes, null PKs, encoding edge cases) into `data/phase1_csv/`.
- `scripts/load_phase2_hop.py` — env-driven loader: CSV→landing (COPY),
  landing→integration and integration→target apply the workbook transforms
  through the real engine (`mapped_rows`) into the next database — mirrors
  `load_phase2_hop.py`, refuses non-demo targets.
- Grants stay least-privilege: loader uses `recon_rw`, suites use `recon_ro`.

### P2-5 — Suite wiring + chain evidence
- `tests/migration` runs ×3 with hop envs — no new Robot code.
- `pipeline_id`/`hop` in env `run:` block → flows into run_summary meta +
  `migration_summary.xlsx` Run Info (audit chain).
- Optional `09_hop_chain` suite later: asserts hop counts reconcile
  (CSV rows = landed = integrated = snapshotted) via trailer/controls
  evidence — full-chain reconciliation on top of per-hop proofs.

### P2-6 — Negative + quality gates
- Per-hop negative fixture set (the corrupted CSV files already exercise
  hop-1 negative path; defective landing rows prove detection at hops 2–3).
- Unit tests for new safe funcs + translator (round-trip Excel SQL →
  expression → evaluated result).
- CI: second postgres service already present; extend provisioning step for
  the three databases; add `-v` suite runs for all three hops.
- Gates: `verdict=PASS`, `coverage=COMPLETE` on all three hop runs.

## Known spec gaps in Phase1.xlsx (to resolve during P2-3)

- 7 blank rows and one "Complex Logic - details shared in next tab" row —
  target field undeclared → will fail closed until specified.
- `CUP00301.CUSIC` row maps to **two** target columns (APPL_KEY, NAICS_CD) —
  needs splitting into two FieldMap rows.
- `DIM_REFERENCE` is a lookup join (not a direct table map) — needs the
  multi-source FieldMap extension or a pre-materialized join view.
- `REGEXP_LIKE` validation — decide: add safe `regexp_like` func or reject.
- `LOAD_DATE` provenance — declare as run parameter (see P2-2).
- `Source_Mapping_Type`/`Integration_Mapping_Type` values (Derived/Direct/
  Derivative Logic) are informational — engine doesn't need them, but keep
  them in workbook metadata for audit traceability.

## Exit criteria

1. `robot tests/migration` ×3 hop envs → all PASS, coverage COMPLETE.
2. CSV negative files provably detected at hop 1; injected row defects at
   hops 2–3 detected by `90_negative`.
3. Chain totals reconcile: CSV source rows = landed = integrated =
   snapshotted (per mapping).
4. Evidence shows pipeline_id + hop + snapshot ids on every run.
5. Same commands work unchanged when UAT swaps in real DB connection vars —
   zero code changes, only env/workbook data.
