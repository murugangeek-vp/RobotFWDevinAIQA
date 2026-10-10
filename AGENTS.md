# QA Automation — Banking Data Reconciliation Framework

Robot Framework + Python framework that proves a bank data migration
(source → target) is 100% correct: every row arrived, every column value
matches, every transformation recomputes, nothing leaked out of scope.
Contract-driven — suites contain zero hardcoded column logic.

## Architecture (respect the dependency direction)

- `libs/engine/` — pure comparison / rules / schema logic. No I/O, no DB
  connections, no Robot imports.
- `libs/adapters/` — `SourceAdapter` / `TargetAdapter` / writer ABCs +
  `@register_*` registry (postgres + snowflake today), `staging.py` external
  canonical sort, `catalog.py` introspection. All source/target I/O lives here.
- `libs/robot/ReconciliationLibrary.py` — thin Robot keyword layer that
  composes engine + adapters (pilot suites). `MigrationLibrary.py` — the
  workbook-driven, verify-only counterpart for `tests/migration`.
- `libs/excelrules/` — workbook-driven business rules
  (`data/rules/business_rules.xlsx` is the source of truth).
- `libs/mapping/` — Excel mapping workbook → contract compiler
  (`data/mapping/bank_mapping.xlsx` for bank→bank migration).
- `libs/reporting/` — migration evidence (`migration_summary.xlsx`,
  `migration_report.md`, `run_summary.json`; `production_signoff` is never
  granted by a run).
- `config/contracts/*.yaml` — data contracts, validated against
  `contract.schema.json` at load (fail closed).
- `config/environments/*.yaml` — environment wiring; credentials only via
  `${ENV_VAR}` references. `webster_santander_live.yaml` (non-fake) requires
  `run.snapshot_id` — the agreed frozen migration cut.
- `resources/keywords/` — composed Robot keywords.
- `tests/pilot/` — contract-driven suites (`01_`–`07_`, `90_` negative-path).
- `tests/migration/` — verify-only bank→bank suites: no loader, target is
  validated as-is; `90_` proves detection on streamed copies only.

## Non-negotiable rules

1. **Verification is read-only.** All compare/verify paths connect as
   `recon_ro` (SELECT-only, enforced by the DB role). Only the fixture loader
   uses `recon_rw`; tests that seed defects are tagged `requires_write`.
2. **No secrets in the repo.** Credentials come from env vars or git-ignored
   `.env` (`RECON_RO_*`, `RECON_RW_*`, `SNOWFLAKE_*`). Config files hold
   `${VAR}` references only. gitleaks runs in CI — never commit passwords,
   DSNs, keys, or `.env`.
3. **No real customer data.** All fixtures are synthetic and deterministically
   generated (`scripts/generate_*.py`, faker + seed). Never commit real
   account numbers, PANs, names, or any PII/financial data.
4. **Fail closed.** Unknown contract keys, rule types, adapters, filter
   columns, env refs → error at load time. Never silently skip a check.
5. **Contract is the single source of truth.** A new feed = a new contract
   YAML + fixture; the same suites apply (no per-feed test code). New
   contracts get coverage in the same sprint (TODO X-02).
6. **Parameterized, identifier-vetted SQL only.** Values are bound params,
   never interpolated; identifiers are validated against the
   contract/whitelist before use.
7. **Verify-only is the production semantic.** `RECON_SKIP_LOAD=true` must
   keep working — verification never depends on write credentials.
8. **Zero-tolerance for data mismatch.** A run is a release gate: report exact
   counts (`missing_in_target`, `extra_in_target`, `diffs`, rule failures) —
   never round, approximate, or swallow a discrepancy.

## Quality gates (run before committing)

```bash
pre-commit run --all-files                    # ruff, ruff-format, mypy, yaml/json
python -m unittest discover -s tests/pilot -p "test_*.py" -v
python -m unittest discover -s tests/migration -p "test_*.py" -v
.venv/Scripts/robot -d results tests/pilot    # must stay green on clean fixtures
.venv/Scripts/robot -d results/migration tests/migration  # needs webster
    # schema seeded + emulated target loaded (scripts/load_migration_target.py)
```

Style: Python 3.11+, ruff line-length 100 (E/F/W/I/B/UP/SIM), mypy clean on
`libs/`. Keep the repo runnable — never break `robot tests/pilot`.

## Skills

Project skills live in `.devin/skills/` — use them for common work:

- `/recon-run` — run suites (full / single / tagged / verify-only / negative)
- `/new-contract` — onboard a new entity or feed end-to-end
- `/new-adapter` — add a source or target adapter
- `/excel-rules` — extend the business-rules workbook or add a rule type
- `/bank-migration-verify` — bank→bank migration validation checklist
- `/security-review` — pre-merge security & data-privacy checklist

Detailed rules in `.devin/rules/` apply automatically by file location.
