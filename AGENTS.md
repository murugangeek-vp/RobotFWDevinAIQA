# QA Automation — Banking Data Migration Validation Framework

Robot Framework + Python framework that proves a bank data migration
(source → target) is 100% correct: every row arrived, every column value
matches, every transformation recomputes, nothing leaked out of scope.
Workbook/contract-driven — suites contain zero hardcoded column logic.

## Phase-1 showcase

The repo currently demonstrates a three-hop migration pipeline defined by
the client spec `data/trailer/Phase1.xlsx`:

| Hop | Source → Target | Env / Workbook |
|---|---|---|
| 1 | CSV extracts → `wdr_uat_active_stage.signature` (landing) | `phase1_hop1_csv_stage.yaml` / `phase1_hop1_csv_to_stage.xlsx` |
| 2 | `signature` → `wdr_uat_integration.wdr_il_core` | `phase1_hop2_stage_il.yaml` / `phase1_hop2_stage_to_il.xlsx` |
| 3 | `wdr_il_core` → `wdr_uat_consumption.wdr_cl_fds_core` | `phase1_hop3_il_consumption.yaml` / `phase1_hop3_il_to_consumption.xlsx` |

The same `tests/migration` suites run against each hop — one hop = one env
YAML + one mapping workbook. `phase1_hop1_csv_stage_negative.yaml` points
at corrupted fixtures to prove defect detection.

## Architecture (respect the dependency direction)

- `libs/engine/` — pure comparison / rules / schema logic. No I/O, no DB
  connections, no Robot imports.
- `libs/adapters/` — `SourceAdapter` / `TargetAdapter` / writer ABCs +
  `@register_*` registry (postgres + snowflake built-in; mysql/oracle/mssql
  via optional drivers), `staging.py` external canonical sort,
  `catalog.py` introspection. All source/target I/O lives here.
- `libs/robot/ReconciliationLibrary.py` — env/credential plumbing shared by
  the Robot layer. `MigrationLibrary.py` — the workbook-driven, verify-only
  keyword library for `tests/migration`.
- `libs/excelrules/` — workbook-driven business rules engine (reusable
  beyond Phase 1).
- `libs/mapping/` — Excel mapping workbook → contract compiler
  (`data/mapping/phase1_*.xlsx`), `sql_transform.py` translates client SQL
  snippets into the safe transform expression language.
- `libs/reporting/` — migration evidence (`migration_summary.xlsx`,
  `migration_report.md`, `run_summary.json`; `production_signoff` is never
  granted by a run), the consolidated end-to-end report (`end_to_end_summary.xlsx`,
  `end_to_end_report.md`) and traceability (`rtm.xlsx`,
  `compliance_scorecard.xlsx`, `defect_report.md` on failures).
- `config/contracts/contract.schema.json` — the contract schema every
  compiled mapping is validated against (fail closed).
- `config/environments/phase1_*.yaml` — one env per hop; credentials only
  via `${ENV_VAR}` references. `run.snapshot_id` is the agreed frozen
  migration cut.
- `resources/keywords/` — composed Robot keywords.
- `tests/migration/` — verify-only suites: `01_end_to_end_migration_testing` runs the
  whole chain as five lifecycle tests from one workbook →
  `end_to_end_summary.xlsx` (business rules live on the workbook's `Rules`
  sheet; `Business Rule` columns carry plain-English explanations);
  `90_negative_path` is the exact-count defect-detection proof. No
  loader — the target is validated as-is.
- `scripts/` — `generate_phase1_workbooks.py` (spec → workbooks),
  `generate_phase1_csv.py` (synthetic fixtures + trailers),
  `provision_phase1.py` (schemas/grants/read-only join views),
  `load_phase1_hop.py` (env-driven fixture loader, RW role).

## Non-negotiable rules

1. **Verification is read-only.** All compare/verify paths connect as
   `recon_ro` (SELECT-only, enforced by the DB role). Only the fixture
   loader uses `recon_rw`; tests that seed defects are tagged
   `requires_write`.
2. **No secrets in the repo.** Credentials come from env vars or git-ignored
   `.env` (`RECON_RO_*`, `RECON_RW_*`). Config files hold `${VAR}`
   references only. gitleaks runs in CI — never commit passwords, DSNs,
   keys, or `.env`.
3. **No real customer data.** All fixtures are synthetic and
   deterministically generated (`scripts/generate_phase1_csv.py`). Never
   commit real account numbers, PANs, names, or any PII/financial data.
4. **Fail closed.** Unknown contract keys, rule types, adapters, filter
   columns, env refs → error at load time. Never silently skip a check.
5. **Workbook is the single source of truth.** A new feed = a new workbook
   mapping (or contract) + fixtures; the same suites apply — no per-feed
   test code.
6. **Parameterized, identifier-vetted SQL only.** Values are bound params,
   never interpolated; identifiers are validated against the
   contract/whitelist before use.
7. **Verify-only is the production semantic.** `RECON_SKIP_LOAD=true` must
   keep working — verification never depends on write credentials.
8. **Zero-tolerance for data mismatch.** A run is a release gate: report
   exact counts (`missing_in_target`, `extra_in_target`, `diffs`, rule
   failures) — never round, approximate, or swallow a discrepancy.

## Quality gates (run before committing)

```bash
pre-commit run --all-files                       # ruff, ruff-format, mypy, yaml/json
python -m unittest discover -s tests/migration -p "test_*.py" -v
# Phase-1 full chain (needs the three wdr_uat_* databases provisioned):
.venv/Scripts/python scripts/generate_phase1_workbooks.py
.venv/Scripts/python scripts/generate_phase1_csv.py
.venv/Scripts/python scripts/load_phase1_hop.py --hop 1
.venv/Scripts/python scripts/provision_phase1.py --views stage
.venv/Scripts/python scripts/load_phase1_hop.py --hop 2
.venv/Scripts/python scripts/provision_phase1.py --views il
.venv/Scripts/python scripts/load_phase1_hop.py --hop 3
.venv/Scripts/robot -d results/phase1/hop1 \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
# ... same pattern for hop2 / hop3 — see docs/RUNBOOK.md
```

Style: Python 3.11+, ruff line-length 100 (E/F/W/I/B/UP/SIM), mypy clean on
`libs/`. Keep the repo runnable — never break `robot tests/migration`.

## Skills

Project skills live in `.devin/skills/` — use them for common work:

- `/recon-run` — run suites (full / single / tagged / verify-only / negative)
- `/new-contract` — onboard a new entity or feed end-to-end
- `/new-adapter` — add a source or target adapter
- `/excel-rules` — extend the business-rules workbook or add a rule type
- `/bank-migration-verify` — bank→bank migration validation checklist
- `/security-review` — pre-merge security & data-privacy checklist

Detailed rules in `.devin/rules/` apply automatically by file location.
