---
name: recon-run
description: Run the reconciliation suites (full, single, tagged, verify-only, negative-path) and interpret results
argument-hint: "[suite-or-test] [--verify-only] [--negative]"
allowed-tools:
  - read
  - grep
  - glob
  - exec
---

RUN THE RECONCILIATION SUITES

All commands run from the repo root using the venv executables
(`.venv/Scripts/robot`, `.venv/Scripts/python` on Windows). Prereq: PostgreSQL
`recon` reachable + `.env` filled (see docs/RUNBOOK.md).

## Pick the run

1. **Full suite (default):** `.venv/Scripts/robot -d results tests/pilot`
   Clean fixtures → expect all PASS, rc=0.
2. **One suite:** `robot -d results tests/pilot/04_record_comparison.robot`
3. **One test:** `robot -d results --test "No Records Missing In Target" tests/pilot/04_record_comparison.robot`
4. **Different source:** add `-v SOURCE_FILE:data/samples/<file>.csv`
5. **Different env/contract:** `-v ENV_FILE:config/environments/<env>.yaml`
6. **Verify-only (production semantics):** set `RECON_SKIP_LOAD=true` in
   `.env`, then `robot --exclude requires_write -d results tests/pilot`.
   The target is validated as-is via `recon_ro` only — no writes, no RW
   credential needed.
7. **Negative path (prove detection):**
   `robot -d results_neg -v SOURCE_FILE:data/samples/customer_bad_dq.csv tests/pilot/02_data_quality.robot`
   and `tests/pilot/90_negative_path.robot` (seeds defects, asserts exact counts).
8. **Engine unit tests:** `python -m unittest discover -s tests/pilot -p "test_*.py" -v`

## Interpret failures (map to layer first)

| Failed suite | Layer to check |
| --- | --- |
| 01_header_metadata | source extract — column names/order vs contract |
| 02_data_quality | source data — read `rule_id` + samples in the message |
| 03_schema_validation | target schema drift — type/nullability/PK |
| 04_record_comparison | load layer — `missing_in_target` / `extra_in_target` keys |
| 05_transformations | mapping layer — `Get Transform Diffs` output |
| 06_pipeline | whole flow — read earlier failures first |
| 07_excel_rules | business-rules workbook — check `results/business_rules_summary.xlsx` |
| `permission denied` | wrong role — verify must be `recon_ro`, loader `recon_rw` |

## Report

- Artifacts: `results/{report.html,log.html,output.xml,run_summary.json}`.
- Summarize: environment, contract, target count, mismatch count, rule
  failures, final PASS/FAIL — the five headline metrics.
- On FAIL: name the layer, the exact mismatch counts, and the first likely
  cause. Never report a reconciliation result as "close enough" — a count is
  either exact or it is a defect.
- In verify-only mode, remember `requires_write` tests were excluded — say so.
