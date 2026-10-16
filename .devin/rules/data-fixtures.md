---
description: "Synthetic-data and fixture rules for data/ and generator scripts"
trigger: glob
globs: "data/**,scripts/generate_*.py"
---

# Test Data & Fixtures

## Synthetic only — banking data privacy

- All files under `data/` are SYNTHETIC. Never place real customer, account,
  transaction, or bank-employee data here — including "anonymized" exports
  from real systems. Anonymization is judged case-by-case by compliance, not
  by us.
- Fixtures must be deterministically generated (faker + fixed seed) via
  `scripts/generate_*.py` — regenerate, never hand-edit committed samples.
- Realistic shape, fake values: IBAN/account-like strings from generator
  patterns, names/locales from faker — never copied from real records.

## Fixture design

- Every clean baseline fixture has corrupted siblings proving detection:
  `*_bad_header` (renamed column), `*_bad_dq` (seeded rule violations),
  `*_missing_rows` (count mismatch). New feeds follow the same trio so the
  negative-path suites stay honest.
- Seeded defects must be counted exactly — negative tests assert the precise
  number of violations, not "some failures".
- Keep fixtures small and readable (tens/hundreds of rows). Large-volume
  benchmarks (PROD-08, 1M-row ceiling) are generated at run time, not
  committed.
- `data/mapping/phase1_*.xlsx` workbooks are config-as-code: regenerate via
  `scripts/generate_phase1_workbooks.py` from the client spec
  (`data/trailer/Phase1.xlsx`); don't edit binary workbooks ad hoc — the
  generator is the reviewable source.
- `results/` (robot reports, run_summary, fakesnow DBs) is output, not
  fixture — git-ignored; never commit run artifacts.
