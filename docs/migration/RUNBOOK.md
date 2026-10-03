# Migration Verification — Runbook (Webster S3 → Santander Snowflake)

The framework is **verification-only**: the bank ETL loads the target; we read the
source extract and the target with read-only identities and produce per-table evidence.

## Layout

| Path | Contents |
|---|---|
| `config/migration/*.yaml` | manifest: tables in scope, tiers, dependencies, relationships |
| `config/contracts/migration/*.yaml` | per-table contract: columns, keys, DQ rules, mappings, controls, `pii` flags |
| `config/environments/migration_*.yaml` | S3 + target connection per environment (no secrets) |
| `tests/migration/` | generic suite + negative-path proofs + unit tests |
| `results_migration/` | Robot report + `migration_report.json` + `.sha256` |

## 1. Local verification (synthetic, no credentials beyond the dev .env)

```powershell
# regenerate fixtures if needed
.\.venv\Scripts\python.exe scripts\generate_bank_samples.py

# unit tests
.\.venv\Scripts\python.exe -m unittest discover -s tests/migration -p "test_*.py" -v

# list the expanded tests without executing (one per table x check)
.\.venv\Scripts\python.exe -m robot --dryrun `
  --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml `
  tests/migration/01_table_verification.robot

# run: S3 stub -> PostgreSQL stand-in (recon-db must be up; .env supplies recon_ro/rw)
.\.venv\Scripts\python.exe -m robot -d results_migration `
  --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml `
  tests/migration/01_table_verification.robot

# negative proofs (defect injection; local synthetic only)
.\.venv\Scripts\python.exe -m robot -d results_migration_negative tests/migration/90_negative_path.robot

# verify the evidence hash
.\.venv\Scripts\python.exe -c "from libs.migration.audit import verify_report as v; print(v('results_migration/migration_report.json'))"
```

Current local baseline: **23 verification tests + 7 negative tests + 30 unit tests**, all
passing; report overall status `PASS`.

## 2. Bank environment run (real S3 -> Snowflake)

1. Get the answers to Q1–Q10 in `ARCHITECTURE.md`; generate contracts and update the
   manifest from the approved mapping spec.
2. Provision identities:
   - AWS: a role with `s3:GetObject`, `s3:GetObjectVersion`, `s3:ListBucket` on the
     extract prefix only.
   - Snowflake: a SELECT-only verification role; key-pair auth; the private key stays
     on the approved runner.
3. On the bank-approved runner, set the variables referenced by
   `config/environments/migration_snowflake.yaml`
   (`WEBSTER_S3_*`, `SNOWFLAKE_*`, `RECON_RO_USER`, `RECON_MASK_KEY`).
   A missing variable fails the run — never hardcode values into the file.
4. Run on the approved runner:

   ```powershell
   $env:MIGRATION_ENVIRONMENT = "mock-migration-1"
   robot -d results_migration `
     --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml `
     --variable MIGRATION_ENV:config/environments/migration_snowflake.yaml `
     tests/migration/01_table_verification.robot
   ```

5. Hand over `results_migration/migration_report.json` + `.sha256` + Robot reports as
   the sign-off evidence. Store them in the bank's immutable evidence store.

## 3. Controls every run enforces

- **PII masking**: `pii: true` values appear as `***REDACTED***`, or `hmac:<token>` when
  `RECON_MASK_KEY` (>= 32 chars) is set; keyed mode correlates the same value across
  tables without exposing it. Verified: no raw PII in `output.xml` or the report.
- **Lineage**: every read is tied to S3 key + ETag + VersionId (versioning required by
  `require_object_versioning`).
- **Fail closed**: empty prefixes, mismatched part headers, unknown codes, missing env
  vars, row bounds exceeded, bucket-owner mismatch, non-local fixture keywords.
- **Pushdown only**: target queries are generated aggregates/anti-joins from
  identifier-validated names — arbitrary SQL stays disabled on Snowflake.
- **Report integrity**: `.sha256` sidecar; edit the report and `verify_report` fails.
- **No partial PASS**: a missing check makes a table `INCOMPLETE`, never green.

## 4. Adding a table

1. Add the extract under the agreed prefix, run the ETL into the target.
2. Author `config/contracts/migration/<table>.yaml` (Phase 2 will draft these
   automatically; a person still reviews `pii`, `controls`, `mappings`).
3. Add one entry (and any relationships) to the manifest.
4. Re-run — tests for the table appear automatically.

## 5. Known boundaries (Phase 2 items)

- Full record comparison is bounded (`max_rows`, default 100000) — tables above it are
  verified at L0–L4 until chunked hashing (MIG-P2) lands.
- Multi-file JSON sources and composite foreign keys are not yet supported.
- Balance proof (opening + transactions = closing) needs source fields — Q6.
- Trial Snowflake acceptance requires a real account; trial accounts block the managed
  MCP SQL tool and our Snowflake writer remains intentionally disabled everywhere.
