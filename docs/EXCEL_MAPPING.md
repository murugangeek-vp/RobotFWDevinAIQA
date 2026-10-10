# Excel mapping workbook — `data/mapping/bank_mapping.xlsx`

The workbook is the **single source of truth** for what migrates and how:
table pairs, keys, scope filters, field renames, types and transformations.
No per-table test code exists — the suites compile every enabled row into a
validated contract at runtime.

Regenerate the demo workbook:

```bash
.venv/Scripts/python scripts/generate_mapping_workbook.py
```

## Sheets

### `Tables` — one row per table pair

| Column | Required | Meaning |
|---|---|---|
| Mapping ID | yes | unique handle, used by `-v MAPPING_ID:...` |
| Source Schema / Source Table | yes | e.g. `webster` / `customer` |
| Target Schema / Target Table | yes | e.g. `PUBLIC` / `customer` |
| Key Columns | yes | comma list of **target-side** key column names |
| Where Clause | no | vetted SQL fragment scoping migrated rows |
| Recon Mode | no | `full` (default) or `hash` (SHA-256 per key — large tables) |
| Batch Size | no | rows per fetch/sort batch (default from env `run.batch_size`) |
| Enabled | yes | `yes`/`no` — `no` rows parse but are never selectable |

### `FieldMap` — one row per target field

| Column | Required | Meaning |
|---|---|---|
| Mapping ID | yes | must reference a `Tables` row |
| Source Column | yes | source column feeding the field |
| Target Column | yes | target column name |
| Target Type | yes | `integer`, `decimal`, `string`, `date`, `timestamp`, `boolean` |
| Nullable | yes | `yes`/`no` |
| Max Length | no | declared VARCHAR length — validated on real Snowflake; marked "not validated" under fakesnow (it reports `TEXT` with no length) |
| Scale | no | numeric scale for `decimal` |
| Transformation | no | engine expression (below); empty = direct rename |
| Enabled | yes | `yes`/`no` |

Every key column must have an enabled FieldMap row. Duplicate target
columns, orphan Mapping IDs and unknown types all fail closed.

### `Mapping` — code translation tables

`Mapping Name, Code, Value` rows referenced by `map(code_col, mapping_name)`
transformations, e.g. `status_codes: A -> ACTIVE`, `product_codes: SAV ->
Savings Account`. An unmapped source code fails the run — never silently
defaults.

### `README` — free text, ignored by the parser.

## Transformation language

Transformations are safe expressions over `{source_column}` placeholders —
parsed by `libs/engine/mapping_transforms.py` (AST-vetted; no arbitrary code).

| Expression | Example | Result |
|---|---|---|
| Direct rename | *(empty)* | `cust_no` → `customer_id` |
| Function | `upper({city})`, `lower({email})` | case conversion |
| Template | `{fname} {lname}` | string concatenation |
| Substring | `substr({ssn}, 7, 4)` | last-4 |
| Date reformat | `date_fmt({dob_txt}, '%Y-%m-%d', '%m/%d/%Y')` | output fmt, input fmt |
| Rounding | `round({bal_amt}, 2)` | |
| Code map | `map(prod_cd, product_codes)` | via `Mapping` sheet |
| Conditional | `casewhen(...)` | decode chains |
| Null default | `ifnull({phone}, 'N/A')` | |

## Fail-closed validation

`parser.load_workbook` raises (never skips) on: missing sheets, missing or
duplicate headers, empty required cells, formula cells, invalid booleans or
integers, unknown target types or recon modes, duplicate target columns,
unsafe WHERE fragments, duplicate mapping IDs, and keys without enabled
FieldMap rows. `Enabled=no` mappings parse (so typos surface) but cannot be
selected or executed.

## Authoring tips

- Keys are **target** names — the FieldMap row for each key defines how the
  source key is produced (rename or transform).
- Use the `Where Clause` for scoped migrations (e.g. `stat_cd = 'A'`); it is
  vetted before reaching the database.
- `Recon Mode = hash` compares SHA-256 of canonicalised rows per key — same
  detection power with less comparison work, but **no reduced network
  transfer** (hashing is client-side).
