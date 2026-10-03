# Runbook — Data Reconciliation Testing Framework

## Prereqs

- Python 3.11+ (3.12 preferred), `uv` or `pip`
- A reachable PostgreSQL 16 with the `recon` database and `recon_rw` / `recon_ro`
  roles. Local Docker:

  ```bash
  docker run -d --name recon-db -e POSTGRES_PASSWORD=postgres_local -p 5432:5432 postgres:16-alpine
  docker exec recon-db psql -U postgres -c "CREATE DATABASE recon;"
  docker exec recon-db psql -U postgres -d recon -f /dev/stdin <<'SQL'   # see scripts/ or the CI workflow for the full grant block
  SQL
  ```

  Full role DDL is in `.github/workflows/reconciliation.yml` (the "Provision
  recon database" step) — reuse it for any fresh environment.

## Setup (< 5 min)

```bash
uv venv .venv && uv pip install --python .venv/Scripts/python -r requirements.txt
# or: python -m venv .venv && .venv/Scripts/pip install -r requirements.txt

# regenerate sample data if needed
.venv/Scripts/python scripts/generate_samples.py
```

## Credentials (never committed)

Copy `.env.example` to `.env` (git-ignored) and fill in the passwords — the
library loads it automatically on suite setup:

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Variables already set in the environment (e.g. CI secrets) override `.env`:

```bash
export RECON_RO_USER=recon_ro   RECON_RO_PASSWORD=...
export RECON_RW_USER=recon_rw   RECON_RW_PASSWORD=...
```

Windows PowerShell: `$env:RECON_RO_USER="recon_ro"` etc.

## Run everything (clean data → PASS, rc=0)

```bash
.venv/Scripts/robot -d results tests/mvp
```

Produces `results/{report.html,log.html,output.xml,run_summary.json}`.

## Run against a corrupted file → expected FAIL

```bash
robot -d results_neg -v SOURCE_FILE:data/samples/customer_bad_dq.csv tests/mvp/02_data_quality.robot
robot -d results_neg -v SOURCE_FILE:data/samples/customer_missing_rows.csv tests/mvp/01_header_metadata.robot
```

`tests/mvp/90_negative_path.robot` proves detection end-to-end (it seeds defects
into the target DB and asserts the exact mismatch counts).

## Switching environment / contract

```bash
robot -d results -v ENV_FILE:config/environments/test.yaml tests/mvp
```

## Common failures

| Symptom | Likely layer |
| --- | --- |
| Header/metadata errors in 01 | source extract |
| Rule failures in 02 | source data quality |
| `table ... does not exist` / type mismatch in 03 | target schema drift |
| `missing_in_target` / `extra_in_target` in 04 | load layer |
| `Get Transform Diffs` non-empty in 05 | transform/mapping layer |
| `permission denied` | wrong role — verification must run as `recon_ro`, loader as `recon_rw` |

## PROD-05 — Snowflake verification

**Status: live trial functional acceptance passed; production security review remains pending.**
The user-run `results_sf_live/output.xml` (2026-10-02 report timestamp) records
14 passed, 0 failed, 0 skipped: 100 source and target rows, zero mismatches.
This proves the real trial connection and reconciliation, not organizational
production access or server-side write denial.
The Snowflake path never loads, truncates, or changes target data. It verifies an
existing table against an independently supplied source extract. The generic
loader, writer registry, and Robot write keywords reject Snowflake. No RW
credentials are needed, despite the generic credential comments in the template.

### Supported scope and safety boundaries

- Conventional unquoted Snowflake identifiers only (folded to uppercase and safely
  quoted in SQL); quoted case-sensitive objects are rejected rather than misread.
- Explicit verification role, `USE SECONDARY ROLES NONE`, and a current-role check.
  These checks are not proof of least privilege. A DBA must review the role and
  inherited/PUBLIC grants: warehouse/database/schema USAGE and table SELECT only,
  without ownership, DML, DDL, or elevated inherited roles. Test server-side denial
  separately in an approved disposable fixture, not against production records.
- Key-pair authentication is the template default; `authenticator: snowflake`
  enables password authentication only where the account's policies allow it.
  TLS verification is not disabled; OCSP fails closed.
- Login/network/socket and statement/queue timeouts are bounded. `max_rows` defaults
  to 100000; an oversized result fails rather than silently reconciling a subset.
  This is a row limit, not a byte/memory or performance SLA. Large-scale streaming
  remains PROD-08 work.
- `NUMBER` integer columns require scale zero; decimal scale, declared precision,
  and declared string lengths are checked. Timestamp contracts currently support
  `TIMESTAMP_NTZ` only; timezone-bearing types fail schema validation.
- Primary keys are discovered with `SHOW PRIMARY KEYS IN TABLE`, ordered by
  `key_sequence`. Snowflake standard-table primary keys are not enforced, so the
  comparator independently rejects null and duplicate keys on either side.
- Use a stable table snapshot/export window: separate metadata/count/data queries
  do not guarantee a single transactional snapshot of a concurrently changing feed.
- Do not pass credentials as Robot command-line variables or store them in YAML.
  Grant access to key files only to the executing service identity. Keep production
  reports access-controlled: existing framework mismatch reports may contain data.

### Configuration and execution

From the repository root, provision these environment variables securely:
`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`,
`SNOWFLAKE_ROLE`, `RECON_RO_USER`, and `SNOWFLAKE_PRIVATE_KEY_FILE`.
For an encrypted key, also set `SNOWFLAKE_PRIVATE_KEY_PASSWORD`. The adapter reads
only the explicitly configured key path; it does not discover keys. For password
authentication, use `RECON_RO_PASSWORD` instead of key-file settings. Endpoint/key
variables must be process environment variables; the local `.env` loader only
loads the existing four `RECON_*` database credentials.

The default environment and contract target `PUBLIC.CUSTOMER`. The administrator
must provision and populate it independently to match the selected source extract.
For another schema/table, supply a matching environment and contract; the library
rejects an environment/contract schema mismatch. `SF_ENV` and `SOURCE_FILE` are
Robot overrides. `SNOWFLAKE_ACCOUNT` uses the connector account identifier, not a URL.

Offline checks (no Snowflake access; detailed unittest results appear in Robot log):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/snowflake -p "test_*.py" -v
.\.venv\Scripts\python.exe -m robot -d results_sf tests/snowflake
```

Live acceptance (only after configuration and DBA approval):

```powershell
$env:SNOWFLAKE_LIVE_TESTS = "true"
.\.venv\Scripts\python.exe -m robot -d results_sf_live tests/snowflake/01_snowflake_target.robot
```

Without the explicit opt-in, the live suite skips. With opt-in, absent configuration,
authentication errors, missing tables, and permission errors fail the run. The
arbitrary-SQL test proves the local adapter guard, not server-side RBAC.

### Fresh trial setup from a personal laptop

Use this flow only in a new personal trial, never an organizational account.
The helper does not provision cloud objects itself: it generates SQL that you
review and execute in Snowsight. It checks the intended account and refuses to
proceed if database RECON already exists. Warehouse/role/user name conflicts also
fail rather than replace existing objects. Snowflake DDL is not atomic; if setup
fails partway through, stop and review the error rather than dropping objects or
rerunning blindly. Run the entire anonymous block together, not selected inner lines.

The account identifier is saved in `config/environments/trial_snowflake.yaml`.
No separate region setting is required for an organization-account identifier.
The helper reads the saved account, so a temporary PowerShell account variable is
not required. This local configuration does not create any Snowflake objects.

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_snowflake_trial.py --create-key
```

Enter and confirm a new passphrase (16+ characters) at the hidden prompts. Keep it
in your password manager. The helper creates a 3072-bit RSA key, encrypted PKCS#8,
under `$HOME\.recon-snowflake-trial\recon_ro.pem`. Never paste that private key or
passphrase into chat, SQL, source control, or a command argument. Only the public
key is included in the generated SQL. Existing keys and output files are never
overwritten. On Windows, restrict access to the key directory to your account:

```powershell
icacls "$HOME\.recon-snowflake-trial" /inheritance:r /grant:r "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F"
```

Review `results_sf/trial_setup.sql`, then open a SQL worksheet in your trial
account, paste its contents and run all statements. ACCOUNTADMIN is used for this
one-time provisioning only. The SQL creates:

- RECON database and PUBLIC.CUSTOMER with the contract's types and primary key.
- RECON_WH X-Small warehouse, initially suspended, auto-suspend after 60 seconds.
- RECON_RO_ROLE with USAGE and SELECT on only the customer table.
- RECON_SF_RO service user using the public key, no password or admin role.
- The repository's synthetic customer rows, transformed independently by SQL
  (concatenation, LOWER, UPPER, decimal rounding, explicit dates/timestamps).

The final LOADED_ROWS must match the contract's expected_row_count. The setup
resumes the warehouse to insert/query the fixture and consumes trial credits;
allow auto-suspend to stop it afterward. A preview without key/user registration
can be generated by omitting `--create-key` and choosing a distinct `--output`.
For an existing generated key, use `--public-key "$HOME\.recon-snowflake-trial\recon_ro.pub"`
and a fresh output path instead of generating another key.

After Snowsight setup succeeds, run:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_snowflake_trial.py --verify
```

Enter the same private-key passphrase. This launches only the live read-only
Snowflake suite with the `trial_snowflake.yaml` profile, setting connection
variables inside the child Python process without changing your PowerShell or
PostgreSQL credentials. Reports go to `results_sf_live`. Never use the generic
MVP suite to provision Snowflake: the framework's Snowflake write paths stay disabled.
Trial acceptance does not establish access to your organization's production account.

### Optional Snowflake MCP verification

The managed Snowflake MCP endpoint can provide an independent second opinion
for metadata and bounded SELECT checks. It is not required for the Robot suite
and does not replace the Python connector evidence.

Generate the trial-local provisioning SQL:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_snowflake_trial.py --mcp-sql
```

Review `results_sf\mcp_setup.sql`, then run all statements in a Snowsight
worksheet for the same trial account. The generator refuses URLs or unexpected
account identifiers; the SQL verifies the fixture count before creating
`RECON.PUBLIC.RECON_MCP_RO` and `RECON_MCP_OAUTH`. The MCP SQL tool is
`read_only: true`, uses `RECON_WH`, has a 60-second timeout, and is granted only
to `RECON_RO_ROLE`. The OAuth integration uses PKCE, disables secondary roles,
allows only `RECON_RO_ROLE`, and registers Devin's loopback callback
`http://localhost:8765/callback`.

The final `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS` result contains the OAuth client ID
and secret. Keep both local. For this checkout, `.devin\mcp_config.local.json`
is Git-ignored and stores the OAuth client ID/secret for this machine only. See
`docs/MCP_SETUP.md` for the exact registration commands.

Authenticate through the browser:

```powershell
devin mcp login snowflake
# If devin is not on PATH:
& "$env:LOCALAPPDATA\Programs\Devin\resources\app\extensions\windsurf\devin\bin\devin.exe" mcp login snowflake
```

Observed trial result: OAuth, MCP `initialize`, and `tools/list` succeed, and
Snowflake advertises the expected `read_only_sql` tool. Invoking that tool is
denied by Snowflake with `Access denied for trial accounts` (399504). The
current Devin/Windsurf gateway also returned HTTP 400 during its own
streamable-HTTP/SSE discovery, while manual MCP JSON-RPC succeeded. Therefore
MCP did not provide row-count or schema query evidence on the trial account; use
the Python/Robot connector report as the trial evidence and repeat MCP checks on
a paid or organizational account. Do not use MCP for writes or as proof of
organization-specific network/RBAC acceptance.

### Reproducible local evidence

Local verification on 2026-10-01: 63 offline Python tests passed; the Snowflake
adapter alone achieved 100% statement and branch coverage with a mocked connector.
The source/PostgreSQL read-only regression passed 25 tests; MySQL read-only
regression passed 11. All 89 Robot test definitions passed dry-run validation
(structure only). Ruff, mypy, and pre-commit passed, including checks on new files.
After updating requests, the installed-environment audit reported no known
vulnerabilities and local HTTP pagination/retry smoke tests passed. The fourteen
live Snowflake tests were skipped in that initial local run. A subsequent user-run
trial execution passed all fourteen, verified from `results_sf_live/output.xml`.
Production-readiness still requires the remaining security and organizational checks.

- `results_sf/output.xml`, `log.html`, `report.html`: offline unit-test wrapper plus
  explicit live skips. Do not treat a PASS aggregate containing skips as live acceptance.
- `results_sf/coverage.xml`: connector-adapter branch coverage, generated using
  `coverage==7.6.10` (development tool, not a runtime dependency).
- `results_sf/dependency-audit.json`: installed-environment vulnerability audit
  using `pip-audit==2.9.0`. The initial audit found PYSEC-2026-2275 in requests
  2.32.4; the runtime pin was updated to the patched 2.33.0 release.
- `results_sf/verification_evidence.json`: initial local checks plus verified live trial results.
- `results_sf_live/output.xml`, `log.html`, `report.html`: 14/14 real Snowflake trial
  checks passed with no skips; retained separately from earlier offline reports.
- `results_sf_missing/output.xml`: intentional negative run showing opt-in plus
  missing `SNOWFLAKE_ACCOUNT` fails before connecting.
- `results_regression/output.xml`: non-mutating source/PostgreSQL regression.
- `results_mysql_readonly/output.xml`: MySQL schema/count/record/transform regression
  against the existing fixture, without reloading it.
- `results_dryrun/output.xml`: keyword/structure validation only, not execution evidence.
- `results_sf/mcp_setup.sql`: generated trial-only MCP provisioning SQL. It is not
  evidence of a connected or authenticated MCP server until the Snowsight setup and
  OAuth sign-in complete.
- `results_sf/mcp_verification.json`: sanitized MCP check showing OAuth,
  initialize, and `tools/list` succeeded while `SYSTEM_EXECUTE_SQL` was denied by
  the trial-account restriction.

Coverage commands (after installing the development tool):

```powershell
.\.venv\Scripts\python.exe -m coverage run --branch --data-file=results_sf/.coverage --source=libs.adapters.snowflake_target -m unittest discover -s tests/snowflake -p "test_*.py"
.\.venv\Scripts\python.exe -m coverage report --data-file=results_sf/.coverage -m
.\.venv\Scripts\python.exe -m coverage xml --data-file=results_sf/.coverage -o results_sf/coverage.xml
```

CI runs the offline wrapper as part of `tests/snowflake` and uploads the standard
Robot artifacts. Live Snowflake configuration is deliberately not added to general
pull-request CI. Production release requires an approved live run and retained DBA
role-review/RBAC evidence; PROD-05 stays unchecked until those checks pass.
