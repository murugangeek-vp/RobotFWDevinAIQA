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

- Suites are numbered `NN_name.robot` under `tests/pilot/`; `90_` prefix is
  reserved for negative-path self-tests that seed defects and assert exact
  detection counts.
- Suite Setup: `Recon Suite Setup` (Load Environment → Load Contract);
  Suite Teardown: `Recon Suite Teardown` (Close Target). Per-suite loaders
  wrap `Load Target From Source`.
- Tests that write to or seed the target MUST be tagged `requires_write` so
  verify-only runs (`RECON_SKIP_LOAD=true robot --exclude requires_write`)
  skip them. Pure verification tests carry no write dependency.
- Parameterize via variables (`${ENV_FILE}`, `${SOURCE_FILE}`) overridable
  with `robot -v`; build paths with `${ROOT}${/}`.
- Expected row counts are dynamic (rows the source delivered post-filter,
  post-transform) — never a literal in a test unless the contract declares
  `metadata.expected_row_count`.

## Authoring via rf-mcp

- When building suites through the Robot Framework MCP server, follow its
  workflow: `analyze_scenario` once → `find_keywords` for any unknown keyword
  (never guess names/args) → `execute_step` one keyword at a time →
  `build_test_suite` to finish.
- Prefer existing `ReconciliationLibrary` keywords; call `find_keywords`
  before inventing a new one (process rule X-01: the keyword library stays
  reusable and documented).

## Results & reporting

- Runs write `results/{report.html,log.html,output.xml,run_summary.json}`;
  `Write Rules Summary` adds `business_rules_summary.xlsx` (audit workbook —
  keep its Run Info sheet fields).
- A suite must leave the target verifiable afterwards: loaders are idempotent
  (TRUNCATE+INSERT) and re-runnable; teardowns close connections.
