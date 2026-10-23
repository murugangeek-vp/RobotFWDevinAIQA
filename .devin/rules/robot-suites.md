---
description: "Robot Framework suite and resource-file conventions"
trigger: glob
globs: "tests/**/*.robot,resources/**/*.resource"
---

# Robot Suite Conventions

## Declarative, contract-driven tests

- `.robot` files contain ZERO column/field logic — the contract drives
  everything. Test bodies call library keywords or composed keywords from
  `resources/keywords/`; never hardcode column names, thresholds, or SQL.
- One test = one assertion aspect (row count, missing keys, extra keys, diffs
  are separate tests). Failure messages must name the rule/column/count and
  include samples — a QA reading `log.html` should locate the layer without
  opening code.

## Structure

- Suites are numbered `NN_name.robot` under `tests/migration/`; `90_`
  prefix is reserved for negative-path self-tests that seed defects and
  assert exact detection counts.
- Suite Setup: `Migration Suite Setup` (Configure Migration — env YAML +
  workbook); Suite Teardown: `Migration Suite Teardown` (Write Migration
  Evidence). The migration suites are verify-only — they never load the
  target; loaders are scripts (`load_phase1_hop.py`) run out-of-band.
- Tests that seed defects on streamed copies MUST stay read-only against
  the databases — defect injection happens on in-memory frames only.
- Parameterize via variables (`${MIGRATION_ENV}`, `${MAPPING_WORKBOOK}`,
  `${MAPPING_ID}`) overridable with `robot -v`; build paths with
  `${ROOT}${/}`.
- Expected row counts are dynamic (rows the source delivered post-filter,
  post-transform) — never a literal in a test unless the contract declares
  `metadata.expected_row_count`.

## Authoring via rf-mcp

- When building suites through the Robot Framework MCP server, follow its
  workflow: `analyze_scenario` once → `find_keywords` for any unknown keyword
  (never guess names/args) → `execute_step` one keyword at a time →
  `build_test_suite` to finish.
- Prefer existing `MigrationLibrary` keywords; call `find_keywords`
  before inventing a new one (process rule X-01: the keyword library stays
  reusable and documented).

## Results & reporting

- Runs write `results/<hop>/{report.html,log.html,output.xml,
  run_summary.json,migration_summary.xlsx,migration_report.md}` — the audit
  workbook's Run Info sheet records timestamp, env, snapshot id, commit,
  executor, verdict.
- A suite must leave the target verifiable afterwards: verification is
  read-only by construction; teardowns close connections and write evidence.
