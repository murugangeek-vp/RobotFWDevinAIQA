---
description: "Banking-grade security and data-privacy rules — applies to every change"
trigger: always_on
---

# Security & Data Privacy (banking domain — non-negotiable)

This framework validates core banking and financial data. Treat every change
as if it will run against production customer data.

## Secrets & credentials

- Credentials live ONLY in env vars or the git-ignored `.env`
  (`RECON_RO_*`, `RECON_RW_*`, `SNOWFLAKE_*`, `RECON_RULES_KEY`). Shell/CI
  values override `.env`.
- YAML/JSON/code may contain `${VAR}` whole-value references only — never a
  literal password, DSN, account number, or key. Unresolvable refs must raise,
  not fall back to a default.
- Never log or print credentials, full connection strings, or `.env` contents.
- Never add real passwords to sample files, docs, tests, or CI YAML — use
  `${{ secrets.* }}` references in workflows.
- `.env`, `*.pem`, `*.key` must stay git-ignored. If a secret is ever
  committed, flag it immediately — it must be rotated, not just removed.

## Least privilege & read-only verification

- All verification/comparison queries run as `recon_ro` — SELECT-only,
  enforced by the database role, not just by convention.
- `recon_rw` is the fixture loader only. Verification code must never open a
  write connection; `RECON_SKIP_LOAD=true` must run with no RW credential.
- New target adapters must expose a read-only path and a separate writer
  factory; never let one connection serve both.
- Tags: tests that write to or seed the target are tagged `requires_write` so
  `--exclude requires_write` verify-only runs skip them.

## SQL safety

- Values are always bound parameters (`query(sql, params)`), never f-string or
  concatenated into SQL.
- Identifiers (table/column/schema names) are validated against the contract
  or an explicit whitelist before reaching SQL — dynamic identifier-vetting is
  the established pattern; keep it.
- `CUSTOM_SQL` rule logic runs as a `WHERE` predicate on the RO connection —
  keep it SELECT-scoped; never extend it to DDL/DML.

## Data privacy

- Fixtures and samples are synthetic only (faker, seeded). No real customer
  names, account numbers, PANs, SSNs, addresses, or balances — in code, docs,
  tests, Excel workbooks, or git history.
- Failure messages may include key values and small samples for diagnosis, but
  never dump full rows of PII into logs or reports.
- `results/` artifacts (reports, `run_summary.json`,
  `business_rules_summary.xlsx`, `fakesnow` DB) can contain compared data —
  they stay git-ignored; treat them as sensitive when sharing.
- `MASK`/`HASH`/`ENCRYPTION` rule types exist for PII columns — prefer them
  over raw-value comparison output for sensitive fields.
- `_demo_cipher` in `libs/engine/reconcile.py` is XOR demo-grade. Do NOT reuse
  or extend it for real data — real encryption must be AES/Fernet behind a KMS
  and flagged for security review.

## Safe execution

- Transform/rule expressions are sandboxed: whitelisted `_SAFE_FUNCS` only for
  transforms; `CUSTOM_PYTHON` has no builtins beyond the approved list. Adding
  a function is a deliberate, reviewed change — never enable arbitrary eval.
- `load_credentials` whitelists which `.env` keys it honors — extend the list
  consciously, never read the whole file into the environment.

## Audit & destructive operations

- Keep audit metadata intact: run summary and `business_rules_summary.xlsx`
  `Run Info` record timestamp, environment, contract version, executor,
  commit, verdict — do not strip these fields.
- TRUNCATE/INSERT happen only through the loader on non-production recon
  tables. Never add code paths that DELETE/DROP/ALTER a target outside the
  loader. Any write to a shared or production-like database requires explicit
  user confirmation first.
