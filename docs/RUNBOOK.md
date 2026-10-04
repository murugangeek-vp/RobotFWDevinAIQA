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
.venv/Scripts/robot -d results tests/pilot
```

Produces `results/{report.html,log.html,output.xml,run_summary.json}`.

## Run against a corrupted file → expected FAIL

```bash
robot -d results_neg -v SOURCE_FILE:data/samples/customer_bad_dq.csv tests/pilot/02_data_quality.robot
robot -d results_neg -v SOURCE_FILE:data/samples/customer_missing_rows.csv tests/pilot/01_header_metadata.robot
```

`tests/pilot/90_negative_path.robot` proves detection end-to-end (it seeds defects
into the target DB and asserts the exact mismatch counts).

## Verify-only mode — `RECON_SKIP_LOAD` (production semantics)

In normal Pilot runs the suite *is* the fixture loader: each test truncates and
reloads expected rows as `recon_rw` before comparing. That keeps tests
deterministic, but it also **overwrites whatever the target already holds** —
a pre-existing defect is erased before it can be seen.

Set `RECON_SKIP_LOAD=true` (shell env or `.env`) to run **verify-only**: every
load is skipped and the suite validates the target as-is using only `recon_ro`.
A missing or broken `RECON_RW_*` credential cannot fail verification.

```powershell
# .env
RECON_SKIP_LOAD=true

# then a plain run is verify-only:
robot --exclude requires_write -d results tests/pilot
```

What changes in verify-only mode:

- `Load Source Into Target` logs `target left untouched` and returns 0 — no
  write connection is opened.
- `Execute Write Sql` connects as `recon_ro`, so the engine itself rejects the
  mutation. This still proves role enforcement (e.g. the Over-Length test now
  fails with `permission denied` instead of `value too long` — either
  rejection passes).
- The 4 negative tests that *seed* defects via target writes are tagged
  `requires_write` — seeding IS a write, so exclude them from verify-only
  runs. To test detection in verify-only mode, tamper the target out-of-band
  (e.g. `UPDATE ... SET city = lower(city)`) and watch the diffs appear.
- `Run Summary Captures Result` (06) still works: the row-count assert only
  applies when rows were actually loaded.

To restore clean fixtures: remove `RECON_SKIP_LOAD` and run the suite once —
the loaders truncate/reload every Pilot table.

**Production verification:** verify-only is the intended mode — the suite
validates the bank-loaded target as-is with a SELECT-only role.

## Migration scope — `source.row_filter`

Bank ETLs often migrate a *subset* of source rows (e.g. active accounts only).
Declare the scope in the contract; rows outside it are out of scope, not
defects — they are excluded from the expected set AND flagged as
`extra_in_target` if the ETL leaks them:

```yaml
source:
  row_filter:
    column: status
    in: [active, "1"]        # operators: in, not_in, eq, ne
  # compound:  all: [{column: a, eq: x}, {column: b, in: [...]}]
  #            any: [...]     allow_empty: true (permit a zero-row scope)
```

`config/contracts/customer_filtered.yaml` demonstrates it (the active subset).
`Get Source Filter Stats` reports `{total, included, excluded}`; with a filter,
the target row-count check expects the *included* count. A filter on an
unknown column fails closed at `Load Contract`; a zero-row scope fails at
`Read Source` unless `allow_empty: true`.

**Row counts are dynamic**: expected target count = rows the source actually
delivered (post-filter, post-transform) — never a contract literal. The
optional `metadata.expected_row_count` remains for feeds with a contractual
fixed size, but the pilot contracts don't use it.

## Excel-driven rules — `tests/pilot/07_excel_rules.robot`

A second, workbook-driven validation layer for migration rules delivered as a
client spreadsheet. `data/rules/migration_rules.xlsx` is the source of truth —
**no per-rule test code**:

- **Rules sheet** — one row per rule: Test ID, Source Table(s), Source
  Column(s), Target Table/Column, Rule Type, Transformation Logic, Expected,
  Severity, Where Clause, Key Column (`src=target` when names differ), Enabled.
- **Mapping sheet** — code tables (`products: LN -> LOAN`, …) for `MAP` rules.
- **Rule types (25)** — `MAX_LENGTH`, `MIN_LENGTH`, `UPPERCASE`, `LOWERCASE`,
  `TRIM`, `CONCAT`, `MAP`, `DIRECT_COMPARE`, `NULL_CHECK`, `DATE_FORMAT`,
  `DATE_TRANSFORM`, `NUMERIC_ROUND`, `DEFAULT_VALUE`, `LOOKUP`, `REGEX`,
  `MASK`, `HASH`, `ENCRYPTION`, `DECRYPTION`, `SUBSTRING`, `PREFIX`, `SUFFIX`,
  `CASE_WHEN`, `CUSTOM_SQL`, `CUSTOM_PYTHON`. Unknown types fail closed at load.
- **Transform Logic** accepts engine expressions: `upper({c})`,
  `substr(c, 12, 4)`, `mask(c)`, `sha256(c)`, `enc(c)`/`dec(c)` (demo cipher —
  `RECON_RULES_KEY`, not for real secrets), `ifnull(c, 'X')`,
  `date_fmt(c, '%d/%m/%Y')`, `round(c, 2)`, `{a}|{b}` templates.
- `CASE_WHEN` syntax: `case(col) when 'v' then 'r' ... else 'd'`.
- `CUSTOM_SQL` runs the Logic as a SQL predicate — `WHERE NOT (<logic>)`
  selects the violating rows server-side.
- `CUSTOM_PYTHON` evaluates the Logic as a Python expression over source
  columns (sandboxed: no builtins beyond int/float/str/len/round/Decimal).
- `LOOKUP` = `lookup(<joined_col>)` over a comma-listed multi-file Source Table
  (e.g. `transform_lab,country_ref` joins on `country`). Added files either
  auto-join on a shared column name or use an explicit
  `file@acc_col=file_col` spec when the keys are named differently —
  `transform_lab,region_ref@country=ctry` joins `transform_lab.country` to
  `region_ref.ctry`.
- The `transform_lab` fixture (`transform_lab.csv` + `country_ref.csv` +
  `config/contracts/transform_lab.yaml`) carries one sample column per rule
  type — regenerated by the suite setup in loader mode.
- `Source Table` accepts a comma list (e.g. `account_extract,account_codes`) —
  files auto-join on the shared key column.
- `Severity` — HIGH/MEDIUM/CRITICAL violations fail; LOW reports only.
- `Enabled=no` rows are parsed but skipped.

Keywords: `Load Rule Workbook` → `Run Migration Validation` (all rules or one
Test ID) → `Get Rule Violations` / `Get Rule Summary` → `Write Rules Summary`
(writes `results/migration_rules_summary.xlsx` — the audit workbook beside the
Robot reports). Target rows are fetched as `recon_ro` through dynamic,
identifier-vetted SQL with an optional WHERE fragment. Regenerate the sample
workbook with `scripts/generate_rules_workbook.py`.

## Switching environment / contract

`config/environments/dev.yaml` points at the local Docker database. For another
Postgres environment, copy it, adjust host/port/database, and pass it in:

```bash
robot -d results -v ENV_FILE:config/environments/dev.yaml tests/pilot
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
