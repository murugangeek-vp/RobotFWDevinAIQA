# Client Intake & Demonstration Guide — Webster → Santander Migration

Practical playbook for onboarding the real migration: how to prove quality to a
skeptical client, what information to collect from both banks, how to configure
each environment, and how a transformation spec becomes executable tests.

---

## 1. Proving quality to a client who has doubts

Never argue — demonstrate. Three exercises, in order:

### 1a. Show the green run

```powershell
robot -d results_migration `
  --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml `
  tests/migration/01_table_verification.robot
```

Walk the client through `results_migration/report.html`: one named test per
(table × check level). Then open `migration_report.json` — per-table status,
S3 VersionIds/ETags, file hashes, operator, git commit. Verify integrity:

```powershell
python -c "from libs.migration.audit import verify_report as v; print(v('results_migration/migration_report.json'))"
```

### 1b. Seeded-defect exercise (the decisive proof)

Ask the client to inject defects **without telling us where** in a mock load —
a changed amount, a dropped row, a wrong code, an orphan record. We then run the
suite and hand back the report; every defect appears, attributed to the right
check level and table, with PII masked. Our own version of this exercise runs
today: `tests/migration/90_negative_path.robot` — 7 injected defects, 7 detected.

### 1c. Independent sample recheck

Client picks N random records; we show for each: the exact S3 object (key +
VersionId + ETag) it was read from, the computed expected value, and the
Snowflake value — from the report lineage and per-column compare. The client can
recompute any field by hand against the published contract.

### What each FAIL looks like

| Level | Client question answered | Example FAIL line |
|---|---|---|
| L0/L1 | "Is the extract itself trustworthy?" | `mapping:status — 12 rows unmapped` |
| L2 | "Did Snowflake build the right table?" | `column 'email' type mismatch` |
| L3 | "Did anything get lost or altered in bulk?" | `balance_by_currency: USD expected 4,203,915.44 != actual 4,203,915.45` |
| L4 | "Are child records orphaned?" | `account_customer: target_orphans 3` |
| L5 | "Are the values right, row by row?" | `key hmac:9f3a…, column balance, expected 1043.21, actual 1043.22` |

If the run is partial, status is `INCOMPLETE` — there is no way to produce a
green report that skipped checks.

---

## 2. Intake checklist — what to collect

### From the SOURCE bank (Webster)

| Area | Questions | Why |
|---|---|---|
| Bucket | Bucket name, AWS account id, region; bucket versioning on? | `source_connection`, `expected_bucket_owner` pin |
| Layout | One file per table or `table/part-*.csv`? Sub-folders for load dates? Delta files? Control/trailer files with counts/totals? | `key`/`prefix`/`suffix`; independent totals |
| Format | Delimiter, encoding, quoting/escaping, header row, compression (gz?), trailer lines? | CSV parsing; parts-consistency checks |
| Access | Cross-account role ARN (preferred) or IAM user? IP/network path from runner? | Standard AWS credential chain |
| Data dictionary | Per table: column names, types, meaning, nullability, business key, uniqueness rules | Contract authoring |
| Code tables | Every coded column's value list (status, product, txn type, segment, country) | `mappings:` + completeness rule |
| Relationships | Documented PK→FK links between tables (or inferred candidates) | L4 referential integrity |
| Volumes | Largest table rows, total extract size, per-table counts | Decides when chunked hashing (MIG-P2) is mandatory |
| Control totals | The totals the bank reconciles internally (balance by ccy/product, counts by status, opening/closing balance rules) | `controls:` entries; balance proof |
| Conventions | Timezone of timestamps; DR/CR flags vs signed amounts; negative conventions; leading zeros; date formats; `NULL` vs empty-string semantics | Avoids false diffs |
| Freshness | Extract schedule; is there a "load complete" marker file? Ordering guarantees between tables? | Run scheduling, snapshot consistency |
| Sensitivity | Per-column classification (PII/confidential); masking policy; rules for storing test evidence | `pii:` flags, evidence handling |

### From the TARGET bank (Santander)

| Area | Questions | Why |
|---|---|---|
| Snowflake | Account identifier, region, database/schema names, warehouse, network policy (allowed IPs/VPN/PrivateLink), session policies | `migration_*` env target block |
| Identity | Verification user name; key-pair auth (we generate keypair, they add the public key); dedicated SELECT-only role + its grant review; secondary roles must be disabled | `RECON_RO_USER`, `SNOWFLAKE_ROLE` |
| Schema | Target table/column names vs source (renames?), data types & widths, declared PKs, extra ETL columns (load_ts, source_system) to exclude from compare | Contract `columns:`/`target:` |
| Load process | What loads the data (tool/Snowpipe/COPY INTO)? How is a load identified (batch id, load_ts)? Re-run semantics (truncate-reload vs merge)? | Result attribution per run |
| Constraints | Which constraints Snowflake declares (PK/UNIQUE/NOT NULL are NOT enforced by Snowflake — our checks are the enforcement) | Expectation-setting |
| Operations | Who approves test runs? Where does the runner execute? Evidence retention/immutability requirements; incident escalation | Sign-off workflow |

### Migration program (both sides)

- Source-to-target **mapping document** (usually Excel): table/column pairs,
  transform expressions, code-mapping tabs — feeds contract authoring/§4.
- Mock-migration cadence (M1, M2…), cutover plan, sign-off criteria per table.
- Rollback/reconciliation expectations: what evidence satisfies an auditor?

---

## 3. Configuring a new environment and running

Framework state is all in YAML — secrets never enter files or commands.

### Step 1 — create the env file (per environment)

Copy `config/environments/migration_snowflake.yaml` to e.g.
`migration_uat.yaml`. Two choices:

- **Parameterized (recommended):** keep `${VAR}` placeholders; set vars per run.
- **Literal:** hardcode non-secret coordinates (bucket, account id, region, db,
  schema, role, warehouse). Secrets stay as env refs regardless.

Set `allowed_data_classifications` and `require_object_versioning` to match the
environment's approval level.

### Step 2 — provision identities

- AWS: attach `s3:GetObject`, `s3:GetObjectVersion`, `s3:ListBucket` on
  `arn:aws:s3:::<bucket>/<prefix>/*` to the runner's role (SSO/role preferred).
- Snowflake: DBA creates `MIGRATION_RO` role → `USAGE` on db/schema/wh +
  `SELECT` on in-scope tables only → service user → add our generated RSA
  **public** key to it → `OAUTH_USE_SECONDARY_ROLES = NONE` equivalent (adapter
  enforces `USE SECONDARY ROLES NONE` itself).

### Step 3 — set environment variables on the runner

```powershell
$env:WEBSTER_S3_BUCKET = "webster-extract-uat"
$env:WEBSTER_S3_REGION = "us-east-1"
$env:WEBSTER_S3_BUCKET_OWNER = "123456789012"       # 12-digit AWS account id
$env:SNOWFLAKE_ACCOUNT = "orgname-acctid"
$env:SNOWFLAKE_WAREHOUSE = "RECON_WH"
$env:SNOWFLAKE_DATABASE = "BANK2_UAT"
$env:SNOWFLAKE_SCHEMA = "MIGRATION"
$env:SNOWFLAKE_ROLE = "MIGRATION_RO"
$env:SNOWFLAKE_PRIVATE_KEY_FILE = "$HOME\.migration\ro.pem"
$env:SNOWFLAKE_PRIVATE_KEY_PASSWORD = (Read-Host)   # or inject via secrets agent
$env:RECON_RO_USER = "MIGRATION_RO_USER"
$env:RECON_MASK_KEY = "<from secret store — ≥32 chars>"
$env:MIGRATION_ENVIRONMENT = "uat"
aws sso login --profile recon-verify   # AWS side
```

### Step 4 — run and read the result

```powershell
robot -d results_migration_uat `
  --prerunmodifier libs/robot/migration_expander.py:config/migration/webster_to_santander.yaml `
  --variable MIGRATION_ENV:config/environments/migration_uat.yaml `
  tests/migration/01_table_verification.robot
```

Reading results:
- `report.html` — per table per level, first failure at top.
- `migration_report.json` — machine-readable status; `overall_status` is
  `PASS` / `FAIL` / `INCOMPLETE`; `.sha256` verifies integrity.
- Triage order: **source-side fails first** (extract problems are Webster's), then
  L2 schema (ETL DDL), then L3 totals (load completeness), then L5 values.
- Show results to the client with §1's demo flow; for sign-off, archive the whole
  `results_migration_*` folder to the immutable store.

Per-env (Dev/QA/UAT/Prod) is just a different env file + credential set — the
same manifest and contracts run unchanged.

---

## 4. Transformation-logic file → executable tests

### Today (manual path)

Send us the bank's mapping workbook and we transcribe it into contracts. The
supported transform vocabulary in `contract.columns[].transform`:

| Spec pattern (typical mapping-doc wording) | Contract expression |
|---|---|
| "concat first + space + last" | `"{first_nm} {last_nm}"` |
| "uppercase/lowercase" | `upper({col})` / `lower({col})` |
| "round to 2dp" | `round({col}, 2)` |
| "trim" | `strip({col})` |
| "code lookup (status tab)" | `map(status_cd, account_status)` + `mappings:` table |
| "1:1 rename/type change" | `source_name:` + `type` |
| "default when null" → not yet built | Phase 2 (`coalesce`) |

Code-mapping tabs become `mappings:` blocks; **every unmapped source value fails
DQ** — the completeness of their spec is itself verified.

### Next (MIG-P1 — contract generator, delivered)

`scripts/generate_contracts.py` converts the bank's mapping workbook (as CSV)
+ code-mapping CSV + real extract headers into **draft** contracts:

```powershell
python scripts/generate_contracts.py `
  --spec mapping_spec.csv --mappings code_mappings.csv `
  --headers-dir <extract-parts> --out-dir config/contracts/migration
```

Spec columns: `contract,target_table,column,source_name,type,nullable,key,pii,transform,scale,max_length,unique`.
The draft is marked `version: 0.1.0-draft` — a reviewer still confirms `pii:` flags
and adds `controls:` before committing. The Robot tests **do not need
regeneration**: the manifest-driven suite expands automatically.

### Guardrail on complex transforms

Transform expressions run through a whitelist (`_SAFE_FUNCS` + templates +
`map()`) — never `eval`. If a bank rule needs e.g. date normalization or
if/then logic, the function is added to the whitelist explicitly and covered by
a unit test before contracts may use it. This is deliberate: contracts are
reviewable, transform code is not executable-by-expression.
