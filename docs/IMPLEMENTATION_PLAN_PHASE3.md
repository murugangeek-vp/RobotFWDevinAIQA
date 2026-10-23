# Phase 3 Implementation Plan — end-to-end Lifecycle Validation (single suite, single template, single report)

## Requirement

One clear test class that runs the migration validation as an end-to-end
lifecycle — requirements intake → source/schema → transformation →
business validation (controls, relations, profile, business rules) →
evidence. No new complex plumbing and **no multiple suites**: everything
merges into the transformations flow.

- **Single input template**: one Excel workbook per hop carries mapping +
  controls + relations + business rules.
- **Single output report**: one consolidated summary per run.
- **Negative path: not required** for this phase.

## Design decisions

1. **The Rules sheet moves inside the mapping workbook.** The mapping
   workbook (`data/mapping/phase1_hop*.xlsx`) gains an optional `Rules`
   sheet using the existing excelrules columns (Test ID, Rule Type,
   Source Table, …). One file = the complete input template. Existing
   standalone rules workbooks keep working — the suite just gets a path.
5. **Every transform/rule is explained in plain English.** The FieldMap
   sheet gains an optional `Business Rule` column carrying a human
   sentence next to each engine expression — e.g. expression
   `casewhen(cupers, 'N', 'C', trim(cupers))` ↔ text "recode customer
   type N to C, otherwise keep the trimmed code". The Rules sheet gains
   the same column next to Transformation Logic. Authored text (not
   auto-translated), ignored by the engine, surfaced in the consolidated
   report beside the expression.
2. **One suite, ordered lifecycle tests.** `tests/migration/
   01_end_to_end_migration_testing.robot` runs the phases in sequence using existing
   keywords only — `Run Migration Stage` for engine stages, the rules
   keywords for business rules. No new stage logic.
3. **One consolidated output.** A thin merger in `libs/reporting/`
   writes `end_to_end_summary.xlsx` (Run Info / Stages / Controls / Rules /
   Violations) + `end_to_end_report.md` in suite teardown, reusing evidence the
   run already produced. Not a new report engine.
4. **Rules stay env-guarded.** The existing
   `Check Rules Apply To Environment` keyword skips the rules phase when
   the workbook's target tables don't exist in the env's schema.

## The test class (exact suite layout)

`tests/migration/01_end_to_end_migration_testing.robot`

| Test case | end-to-end phase | What it runs |
|---|---|---|
| Requirements Intake And Mapping Compile | Analysis | `Run Migration Stage mapping` — workbook → contract, fail-closed |
| Source And Schema Conformance | Design | `Run Migration Stage source`, `dq`, `schema` |
| Transformation And Record Reconciliation | Build/Test | `Run Migration Stage recon`, `transform` — includes all column-level transform checks |
| Business Validation — Controls Relations Profile Rules | Business Test | `controls`, `relations`, `profile` stages + `Run Business Rules Validation` (skipped if no rules in workbook / wrong env) |
| Lifecycle Evidence Report | Release | Teardown writes `end_to_end_summary.xlsx` + `end_to_end_report.md`; test asserts verdict = PASS |

Suite Setup: `Load Environment` + `Configure Migration` +
`Load Rule Workbook ${MAPPING_WORKBOOK}` (same file — the Rules sheet is
found inside it). Suite Teardown: consolidated report writer.

## Steps

### P3-1 — Rules sheet inside the mapping workbook

- `scripts/generate_phase1_workbooks.py`: write a `Rules` sheet into the
  hop-1 workbook — the 11 demo rules live inline in the generator as the
  single source of truth (the separate `data/rules/` workbook and
  `10_business_rules.robot` were removed in cleanup; the lifecycle suite
  is the only rules runner).
- `libs/mapping/parser.py`: ignore the extra sheet (it already reads
  named sheets only — verify no fail-closed on unknown sheet).
- `ReconciliationLibrary.load_rule_workbook` already accepts any path —
  the suite passes the mapping workbook itself.
- Regenerate workbooks.

### P3-1b — Plain-English rule descriptions in the template

- `FieldMap` sheet: add an optional `Business Rule` column — one
  plain-English sentence per row describing the transformation, written
  for a non-engineering reviewer. Generator populates it alongside each
  expression (e.g. `lpad(cussnr, 9, '0')` → "zero-pad the tax id to 9
  digits"; `concat('CBS', 'CUST', cubk, cunbr)` → "party key = 'CBS' +
  'CUST' + bank number + customer number").
- `Rules` sheet: same column next to `Transformation Logic` (e.g.
  `lookup(attribute_value_desc)` → "tax-id-type description must match
  the DIM_REFERENCE decode").
- Engine impact: none — optional column, parsers pass it through or
  ignore it; `end_to_end_summary.xlsx` shows expression + plain-English side by
  side.

### P3-2 — The lifecycle suite

- New file `tests/migration/01_end_to_end_migration_testing.robot` (the ONLY suite this
  phase adds): the five test cases above, env-agnostic via
  `${MIGRATION_ENV}`/`${MAPPING_WORKBOOK}` variables like every other
  suite. Rules phase calls `Check Rules Apply To Environment` first;
  `Run Business Rules Validation source_dir=${RULES_SOURCE_DIR}`.
- Both libraries coexist (GLOBAL scope): `MigrationLibrary` for stages,
  `ReconciliationLibrary` for rules + env loading.

### P3-3 — Consolidated single report

- `libs/reporting/consolidated_report.py`: inputs = `self.evidence` (stage rows)
  + `self.rule_results` + meta; output `end_to_end_summary.xlsx` (Run Info |
  Stage Results | Control Mismatches | Rule Results | Violations) and
  `end_to_end_report.md` — one summary per run.
- `Write Consolidated Evidence` keyword on `ReconciliationLibrary` (or a free
  keyword composing existing writers) called in suite teardown.

### P3-4 — Verification

- Clean hop-1 run: all 5 lifecycle tests PASS; report shows stages +
  rules in one workbook.
- Unit test for the report merger (stage rows + rule results → sheets).
- ruff/mypy/unit tests green; existing suites untouched — CI unchanged.

## P3-5 — Traceability & compliance (reinstated)

The deferred items were **reinstated** and implemented as a thin evidence
layer — no new engine, no new suite:

| Piece | Delivers |
|---|---|
| Requirement IDs | Optional `Requirement ID` column on Tables / FieldMap / Controls / Relations / Rules → contract specs (`requirement_id` allowed in `contract.schema.json`) + `Rule.requirement_id` |
| RTM | `libs/reporting/traceability.py` joins `run.requirements` (env YAML) with stage / control / relation / rule evidence → `rtm.xlsx` (PASS / FAIL / UNCOVERED) |
| DC·DA·DI·DG scorecard | `compliance_scorecard.xlsx` — CONFIRMED / FAILED / NOT COVERED / INSUFFICIENT EVIDENCE |
| Defect report | `defect_report.md` only when defects exist; removed on clean runs |
| Coverage gate | `run.require_rtm_coverage: true` (hop-1) + `Validate Requirements Coverage` keyword |
| Test plan | `scripts/generate_test_plan.py` → `test_plan.xlsx` (one row per requirement) |
| Devin playbook | `docs/DEVIN_PLAYBOOK.md` — repeatable AI-driven execution |

## Explicitly not in this phase

- Multiple new suites — everything lands in the one lifecycle suite.
- UI screenshots (headless validation — evidence workbooks are the
  artifact), in-repo LLM test generation, governance catalog, production
  sign-off.
- No changes to the engine comparison paths.

## Exit criteria

1. `robot tests/migration/01_end_to_end_migration_testing.robot` with hop-1 env +
   workbook → all lifecycle tests PASS end-to-end.
2. One `end_to_end_summary.xlsx` per run contains stages + controls + rules +
   violations; one `end_to_end_report.md` narrates the same verdict.
3. The same file runs under hop-2/hop-3 envs (rules phase skips
   cleanly; the rest of the lifecycle still validates).
4. Input is the single workbook template only — no second rules file
   required.
