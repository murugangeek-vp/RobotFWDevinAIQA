# Migration Verification — Runbook (Webster S3 → Santander Snowflake)

Client-facing playbook (proof-of-quality demo, intake checklists, environment
onboarding, mapping-file intake): [CLIENT_INTAKE.md](CLIENT_INTAKE.md).

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
  `require_object_versioning`) — including the bank trailer file (`control_file`) whose
  declared totals are a third independent check next to source- and target-computed ones.
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

- `compare_mode: auto` (default) does a full row compare up to `max_rows`, then switches
  to **bucketed fingerprint compare**: 256 MD5 key-buckets, per-bucket COUNT + row-hash
  sum pushed down as generated SQL, drill-down only into mismatched buckets. Override
  per table (`compare_mode: full|hashed|none`) or per run (`MIGRATION_COMPARE_MODE`,
  `MIGRATION_HASH_BUCKETS`).

## 6. Delta / incremental verification (MIG-P5)

When the bank loads in batches, restrict every target check to the loaded batch:

```powershell
$env:MIGRATION_BATCH_ID = "M2-2024-05-01"          # batch id from the load run
# env yaml: target.batch_column: load_batch      # the ETL batch column name
```

Row count, control totals, orphans, record compare, and bucket checksums then
read `WHERE <batch_column> = <batch_id>` only. Setting one without the other
fails closed. Batch ids must match `^[A-Za-z0-9_.:-]{1,128}$` (generated SQL,
no parameters — the pattern is the injection guard). Note the semantics: the
source extract must be the *delta file* for that batch, so batch mode verifies
"the delta rows landed" — full-table verification still runs for complete files.

## 7. Parallel and large-manifest runs (MIG-P6)

Shard the manifest across processes — each shard is a complete signed run:

```powershell
$env:MIGRATION_TABLES = "account,transaction"   # this shard's tables
robot -d results_migration_shard2 --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml tests/migration/01_table_verification.robot
```

Relationship checks run in the child's shard. Unknown/disabled table names fail
closed. Keep related tables together. `pabot` is installed for dev, but do NOT
use `--testlevelsplit` on the migration suite: each executor would report only
its own tests, fragmenting the audit evidence.

## 8. Evidence store (MIG-P8)

`Archive Evidence` uploads the bundle (report, `.sha256`, `evidence_manifest.json`
with per-file SHA-256, logs) to `env.evidence.bucket/prefix/run_id/`. Bank envs
should use a separate versioned bucket with Object Lock (immutability) and a
retention policy; the archive itself does not enforce those — the bucket policy does.
- Multi-file JSON sources and composite foreign keys are not yet supported.
- Balance proof (opening + transactions = closing) needs source fields — Q6.
- Trial Snowflake acceptance requires a real account; trial accounts block the managed
  MCP SQL tool and our Snowflake writer remains intentionally disabled everywhere.
