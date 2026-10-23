# Devin Playbook — repeatable demo flow (generate → load → verify → evidence → push → CI)

The Phase-1 showcase is designed so an AI agent (Devin) can run the whole
validation lifecycle end-to-end with no manual steps. This is the
repeatable script of that flow.

## 0. Prerequisites (once per environment)

- PostgreSQL reachable; `recon_rw` (loader) and `recon_ro` (verify) roles
  exist per `.github/workflows/reconciliation.yml` or
  `scripts/provision_phase1.py`.
- Credentials live in `.env` (`RECON_RO_USER/PASSWORD`,
  `RECON_RW_USER/PASSWORD`) — never in files, never logged.
- `.venv` with `requirements.txt` installed.

## 1. Generate fresh synthetic data

```bash
.venv/Scripts/python scripts/generate_phase1_workbooks.py   # spec -> 3 workbooks (incl. Rules sheet)
.venv/Scripts/python scripts/generate_phase1_csv.py         # deterministic fixtures + trailers + negative variants
.venv/Scripts/python scripts/generate_test_plan.py          # workbook -> docs/test_plan.xlsx
```

## 2. Load all three hops (bank ETL simulation)

```bash
.venv/Scripts/python scripts/load_phase1_hop.py --hop 1
.venv/Scripts/python scripts/provision_phase1.py --views stage
.venv/Scripts/python scripts/load_phase1_hop.py --hop 2
.venv/Scripts/python scripts/provision_phase1.py --views il
.venv/Scripts/python scripts/load_phase1_hop.py --hop 3
```

Loaders truncate-and-reload their own tables only; verification is
read-only throughout.

## 3. Run the end-to-end lifecycle suite per hop

```bash
.venv/Scripts/robot -d results/phase1/hop1 --xunit xunit.xml \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
# same pattern for hop2 (phase1_hop2_stage_il.yaml + hop2 workbook)
# and hop3 (phase1_hop3_il_consumption.yaml + hop3 workbook)
```

Expected: 6/6 PASS per hop (5 lifecycle tests + negative self-check).

## 4. Verify the defect-detection proof (expected failure)

```bash
.venv/Scripts/robot -d results/phase1/hop1_negative \
  -v MIGRATION_ENV:config/environments/phase1_hop1_csv_stage_negative.yaml \
  -v MAPPING_WORKBOOK:data/mapping/phase1_hop1_csv_to_stage.xlsx tests/migration
grep '"extra": 5' results/phase1/hop1_negative/run_summary.json
```

A *passing* negative run is itself a defect — it must FAIL with exact counts.

## 5. Collect evidence

Per hop under `results/phase1/hopN/`:

| Artifact | Purpose |
|---|---|
| `end_to_end_summary.xlsx` | consolidated report — reviewer sheets (stages, controls, rules w/ plain-English, violations, RTM, scorecard) + QA diagnostics (data diffs, queries, column profile, metadata, insights) |
| `end_to_end_report.md` | same verdict, narrative form |
| `rtm.xlsx` | requirement → check traceability (COVERED-PASS/FAIL/UNCOVERED) |
| `compliance_scorecard.xlsx` | DC/DA/DI/DG per-dimension verdicts |
| `defect_report.md` | present only on failing runs — counts, violating keys, audit SQL, owner hints |
| `run_summary.json` | machine-readable verdict + rtm/compliance blocks |
| `report.html`/`log.html`/`output.xml`/`xunit.xml` | Robot evidence |

## 6. Merge the pipeline report, commit, push, watch CI

```bash
.venv/Scripts/rebot --merge -d results/phase1/merged --name "Phase-1 Migration" \
  results/phase1/hop1/output.xml results/phase1/hop2/output.xml results/phase1/hop3/output.xml
git add -A && git commit -m "..." && git push origin <branch>
# then poll https://github.com/<org>/<repo>/actions — every step green
```

## Quality gates before commit

```bash
.venv/Scripts/pre-commit run --all-files     # ruff, ruff-format, mypy, yaml/json, gitleaks
.venv/Scripts/python -m unittest discover -s tests/migration -p "test_*.py" -v
.venv/Scripts/robot --dryrun -d results/dryrun tests/migration   # CI's pre-flight
```

## Rules of the demo

- Verify-only: the suites never write to any target; `recon_ro` enforces it.
- `RECON_SKIP_LOAD=true` runs must work with no RW credential.
- Fix the data, not the assertion: if a stage fails, reload/re-derive the
  target — never loosen a check to make it pass.
- Snapshot discipline: regenerated CSVs must be reloaded before verifying
  (fixtures and target must come from the same generation run).
