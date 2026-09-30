# Data Contracts

Contracts live in `config/contracts/*.yaml` and are validated against
`config/contracts/contract.schema.json` at load time (`libs/engine/schema.py`).

## customer.yaml

Reconciles `data/samples/customer.csv` into `public.customer` (PostgreSQL).

| Source column | Target column | Notes |
| --- | --- | --- |
| `customer_id` | `customer_id` | integer, PK, unique |
| `first_name` + `last_name` | `full_name` | transform `{first_name} {last_name}` |
| `email` | `email` | transform `lower(...)`; regex-checked |
| `dob` | `dob` | date `%Y-%m-%d` |
| `country` | `country` | transform `upper(...)`; ISO-2 |
| `balance` | `balance` | decimal, scale 2, range [0, 1e6] |
| `status` | `status` | enum: active / inactive / suspended |
| `created_at` | `created_at` | timestamp `%Y-%m-%d %H:%M:%S` |

## Sample data (`data/samples/`)

| File | Purpose |
| --- | --- |
| `customer.csv` | 100 valid rows — the clean baseline |
| `customer_bad_header.csv` | `email` renamed to `email_address` — header check must fail naming the column |
| `customer_bad_dq.csv` | 5 seeded violations: null email, non-integer id, duplicate id, out-of-range balance, invalid status |
| `customer_missing_rows.csv` | 99 rows — row-count metadata check must fail |

Regenerate with `.venv/Scripts/python scripts/generate_samples.py` (deterministic,
seeded).

## Authoring a new contract

1. Copy `customer.yaml`, change `contract_name`, `source.columns`, `columns`,
   `keys`, and `target.table`.
2. Validate: the contract is checked against `contract.schema.json` every time
   `Load Contract` runs — an invalid contract fails fast.
3. Transforms support `"{col}"` templates and the whitelisted functions
   `lower()`, `upper()`, `round(x, nd)`, `strip()` — see
   `libs/engine/reconcile.py:_SAFE_FUNCS`. Add new functions there deliberately;
   arbitrary code is not evaluated.
4. Point a suite at the new contract via `-v ENV_FILE:...` (env yaml holds the
   `source.contract` path).
