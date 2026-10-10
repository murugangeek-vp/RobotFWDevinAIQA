---
name: security-review
description: Pre-merge security & data-privacy checklist for changes to this banking reconciliation framework
allowed-tools:
  - read
  - grep
  - glob
  - exec
---

SECURITY & DATA-PRIVACY REVIEW

Run `git status` + `git diff` (staged and unstaged), then walk this checklist.
Report findings as PASS/FAIL per item with file:line evidence.

## 1. Secrets

- [ ] No passwords/keys/DSNs/tokens in the diff — check literals like
  `password`, `passwd`, `secret`, `token`, `account` in added lines; scan
  .env-style assignments.
- [ ] `.env`, `*.pem`, `*.key` not staged (gitignore'd — verify anyway).
- [ ] Config uses `${VAR}` refs only; no literal credential fallbacks added.
- [ ] CI changes use `${{ secrets.* }}`, never inline values.
- [ ] `load_credentials` whitelist not broadened without reason.

## 2. Access control

- [ ] No new write path outside `libs/loader.py` (recon_rw) — grep for
  `INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE` outside the loader and
  seed-only negative tests.
- [ ] Verification paths still connect RO only; nothing added that requires
  RW for verification; `RECON_SKIP_LOAD` behavior preserved.
- [ ] New tests touching the target with writes are tagged `requires_write`.

## 3. Injection & execution

- [ ] New SQL uses bound params; identifiers validated against
  contract/whitelist (grep for f-strings/`%`/`+` in SQL construction).
- [ ] `_SAFE_FUNCS` additions (if any) are pure, deterministic, reviewed.
- [ ] `CUSTOM_PYTHON` sandbox unchanged; `CUSTOM_SQL` still SELECT-scoped.
- [ ] No new `eval`/`exec`/`os.system`/`subprocess` with dynamic input.

## 4. Data privacy

- [ ] New fixtures are synthetic + generated (faker/seed), not copied real
  data; no plausible real PAN/account/customer data in any committed file.
- [ ] Logs/failure messages don't dump full PII rows; key samples only.
- [ ] `_demo_cipher` not extended for real secrets (XOR demo-grade only).
- [ ] No committed artifacts under `results/` or generated `*.duckdb`/`*.xlsx`
  outputs.

## 5. Correctness-as-security

- [ ] New code fails closed (unknown type/key/env ref → error), never
  silently skips a check or defaults-to-pass.
- [ ] Audit fields (run summary, workbook Run Info) intact.

If any item fails, flag it clearly before merge — for a banking validation
tool, a security miss is a release blocker, not a nit.
