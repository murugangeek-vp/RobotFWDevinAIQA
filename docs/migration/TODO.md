# TODO v2 — Webster → Santander Migration Verification

Version 2 of the delivery checklist, scoped to the bank migration
(see [ARCHITECTURE.md](ARCHITECTURE.md)). The v1 checklist (`docs/TODO.md`) stays the
record of the MVP and adapter work.

Tick a box only when its **check** passes. "Local" means synthetic fixtures with an S3 stub
and a PostgreSQL stand-in target; it is not bank acceptance.

## Phase 1 — Framework foundation (this branch)

- [x] **MIG-01 PII masking** — `pii: true` columns masked in logs, failures, report.
  _Check:_ ✅ `Masker` unit tests + 0 raw PII hits in `results_migration/output.xml`
  and `migration_report.json`; `hmac:` tokens when `RECON_MASK_KEY` set, `***REDACTED***` otherwise.
- [x] **MIG-02 Code mappings** — `mappings:` + `map(col, name)` transform; unmapped codes fail.
  _Check:_ ✅ unit tests + negative E2E (`Unmapped Source Code Fails Data Quality`).
  Unquoted non-string YAML codes rejected at contract load.
- [x] **MIG-03 S3 hardening** — lineage (key/VersionId/ETag/size), `expected_bucket_owner`,
  HTTPS-only custom endpoints, `suffix` filter, empty prefix and mismatched part headers fail.
  _Check:_ ✅ moto unit tests + negative E2E (`Part File With Different Header Fails Closed`).
- [x] **MIG-04 Control totals** — count/sum/min/max/null_count/count_distinct, optional
  group-by; target side pushed down as fixed aggregate SQL (Postgres/MySQL/Snowflake).
  _Check:_ ✅ unit tests + E2E `balance_by_currency` catches a +0.01 tamper.
- [x] **MIG-05 Referential integrity** — manifest relationships, orphan counts in source and target.
  _Check:_ ✅ negative E2E (`Orphan Account Fails Referential Integrity`, target orphan masked).
- [x] **MIG-06 Manifest** — schema-validated; unique tables, contracts load, relationships
  reference real columns with matching types, dependency graph acyclic.
  _Check:_ ✅ unit tests assert each rejection reason.
- [x] **MIG-07 Generic suite** — Robot pre-run modifier expands per-table / per-relationship
  tests from the manifest; adding a table needs no suite change.
  _Check:_ ✅ 23 tests generated from 3 tables + 2 relationships, all PASS.
- [x] **MIG-08 Audit report** — run id, git commit, manifest/contract SHA-256, S3 lineage,
  per-table checks and status, report `.sha256`.
  _Check:_ ✅ `migration_report.json` written with status PASS; `verify_report` detects tampering.
- [x] **MIG-09 Synthetic bank fixtures** — customer/account/transaction CSVs (250/400/2000
  rows, multi-part), contracts, manifest, local + Snowflake environments.
- [x] **MIG-10 Local E2E** — moto S3 → PostgreSQL stand-in: 23/23 green; 7/7 negative runs
  detect tampered balance, orphan, unmapped code, header drift, missing row, PII leaks.
- [x] **MIG-11 Quality gates** — 30 migration unit tests, ruff, ruff-format, mypy,
  pre-commit all pass; CI job added (first remote run pending).

## Phase 2 — Scale and bank environment (needs answers Q1–Q10)

- [ ] **MIG-P1** Contract generator: draft contracts from S3 header + Snowflake
  `INFORMATION_SCHEMA` + mapping spec; human review required.
- [ ] **MIG-P2** Chunked/streamed comparison with keyed row hashes in buckets
  (Snowflake `HASH_AGG` vs Python) for tables above `max_rows`; drill-down only on failing buckets.
- [ ] **MIG-P3** Balance proof (opening + transactions = closing) once source fields are known.
- [ ] **MIG-P4** Control/trailer-file support as independent expected counts/totals.
- [ ] **MIG-P5** Delta/incremental extracts and mock-migration run comparison.
- [ ] **MIG-P6** Parallel execution (`pabot`) per table tier; performance SLA benchmarks.
- [ ] **MIG-P7** Live S3 → Snowflake run on a bank-approved host with bank-approved data.
- [ ] **MIG-P8** Central immutable evidence store + dashboard + sign-off workflow.
- [ ] **MIG-P9** DBA review of Snowflake grants (direct, inherited, PUBLIC) and S3 IAM policy.

## Phase gate — Bank acceptance
- [ ] All in-scope tables PASS at the agreed levels on a mock migration in a bank environment.
- [ ] Evidence package reviewed and signed off by the migration lead and data owner.
