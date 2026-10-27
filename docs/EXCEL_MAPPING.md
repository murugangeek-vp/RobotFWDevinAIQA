# Excel mapping workbooks — `data/mapping/phase1_*.xlsx`

The workbook is the **single source of truth** for what migrates and how:
table pairs, keys, scope filters, field renames, types and transformations.
No per-table test code exists — the suites compile every enabled row into a
validated contract at runtime.

## Which workbook is which

One workbook per hop of the Phase-1 pipeline — each pairs with the
same-named `config/environments/phase1_*.yaml`:

| Workbook | Hop | Suite |
|---|---|---|
| `data/mapping/phase1_hop1_csv_to_stage.xlsx` | CSV extracts → `wdr_uat_active_stage.signature` | `tests/migration` |
| `data/mapping/phase1_hop2_stage_to_il.xlsx` | `signature` → `wdr_uat_integration.wdr_il_core` | `tests/migration` |
| `data/mapping/phase1_hop3_il_to_consumption.xlsx` | `wdr_il_core` → `wdr_uat_consumption.wdr_cl_fds_core` | `tests/migration` |

All compile to the same contract model — a mapping declared in any workbook
runs through the same engine regardless of source dialect
(postgres/snowflake/mysql/oracle/mssql or CSV), which is what "unified
rules" means here: adapters translate, the engine is unchanged.

The workbooks are generated from the client spec
(`data/trailer/Phase1.xlsx`); regenerate them:

```bash
.venv/Scripts/python scripts/generate_phase1_workbooks.py
```

## Sheets

### `Tables` — one row per table pair

| Column | Required | Meaning |
|---|---|---|
| Mapping ID | yes | unique handle, used by `-v MAPPING_ID:...` |
| Source Schema / Source Table | yes | e.g. `signature` / `v_dim_customer_src` |
| Target Schema / Target Table | yes | e.g. `PUBLIC` / `customer` |
| Key Columns | yes | comma list of **target-side** key column names |
| Where Clause | no | vetted SQL fragment scoping migrated rows |
| Recon Mode | no | `full` (default) or `hash` (SHA-256 per key — large tables) |
| Batch Size | no | rows per fetch/sort batch (default from env `run.batch_size`) |
| Enabled | yes | `yes`/`no` — `no` rows parse but are never selectable |
| Source Tables | no | comma list of base tables when Source Table is a view — shown in `migration_report.pdf` so reviewers see the real source tables |

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
| Description | no | plain-English meaning of the mapping for reviewers — rendered next to the transformation in `migration_report.pdf`, the `Field Mappings` sheet and `migration_report.md` |

Every key column must have an enabled FieldMap row. Duplicate target
columns, orphan Mapping IDs and unknown types all fail closed.

### `Mapping` — code translation tables

`Mapping Name, Code, Value` rows referenced by `map(code_col, mapping_name)`
transformations, e.g. `status_codes: A -> ACTIVE`, `product_codes: SAV ->
Savings Account`. An unmapped source code fails the run — never silently
defaults.

### `Controls` — aggregate financial controls (optional)

One row per declared control; the `controls` stage recomputes them from the
source (post-transform) and the target, then certifies them against the bank
trailer file (`source.control_file` in the env YAML — CSV `name,group,value`).

| Column | Required | Meaning |
|---|---|---|
| Mapping ID | yes | must reference a `Tables` row |
| Control ID | yes | unique per mapping |
| Type | yes | `count` `sum` `min` `max` `null_count` `count_distinct` |
| Column | for non-`count` | **target-side** column name |
| Group By | no | comma-separated target columns (max 3) |
| File Name | no | certified total's name in the trailer; blank = Control ID |
| Enabled | no | `yes`/`no` (blank = yes) |

When `source.control_file` is configured, **every** declared control needs a
certified trailer total — an uncertified control or a drifted value fails the
stage. Group keys in the trailer join with ` | ` (blank = ungrouped).

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
| Conditional | `casewhen(...)` | decode chains (same-column equality) |
| Searched CASE | `case(eq({a},'P'),'INDIVIDUAL', booland(in_list({t},'C','B'), like(upper({n}),'%LLC%')), 'CORP_SMALL', 'BANK', None)` | per-arm conditions: `eq`/`neq`/`in_list`/`like`/`isnull`/`notnull`/`regexp_match`, combined with `booland`/`boolor`/`boolnot`; all yield `'True'`/`'False'` |
| Null default | `ifnull({phone}, 'N/A')` | |

## Fail-closed validation

`parser.load_workbook` raises (never skips) on: missing sheets, missing or
duplicate headers, empty required cells, formula cells, invalid booleans or
integers, unknown target types, recon modes or control types, duplicate
target columns or control IDs, unsafe WHERE fragments, duplicate mapping
IDs, and keys without enabled FieldMap rows. `Enabled=no` mappings parse (so
typos surface) but cannot be selected or executed.

## Authoring tips

- Keys are **target** names — the FieldMap row for each key defines how the
  source key is produced (rename or transform).
- Use the `Where Clause` for scoped migrations (e.g. `stat_cd = 'A'`); it is
  vetted before reaching the database.
- `Recon Mode = hash` compares SHA-256 of canonicalised rows per key — same
  detection power with less comparison work, but **no reduced network
  transfer** (hashing is client-side).
