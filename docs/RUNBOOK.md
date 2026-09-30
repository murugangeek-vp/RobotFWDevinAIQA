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
