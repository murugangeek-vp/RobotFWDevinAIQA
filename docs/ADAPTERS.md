# Adapters — connecting any database

The framework is config-driven: `source.type` / `target.type` in the
environment YAML pick the adapter at runtime. Working adapters today are
**PostgreSQL** (source + target) and **Snowflake** (source + target). Adding
MySQL, Oracle, Databricks, Salesforce, etc. means adding one module and a
decorator — the engine, parser and Robot suites do not change.

## Registry

`libs/adapters/registry.py` holds three registries. A module registers itself
at import time:

```python
@register_source("mysql")          # env yaml: source.type: mysql
class MySqlSource(SourceAdapter): ...

@register_target("mysql")          # env yaml: target.type: mysql
class MySqlTarget(TargetAdapter): ...

@register_writer("mysql")          # loader mode only (never verification)
def mysql_writer(cfg): ...
```

Register the module in `registry._ensure_builtin()` so plain imports work.

## Contracts an adapter must satisfy

Source (`libs/adapters/base.py::SourceAdapter`):

| Method | Returns | Notes |
|---|---|---|
| `read_batch()` | DataFrame | small volumes only |
| `read_chunks(size)` | generator of DataFrames | **required for scale** — server-side cursor or equivalent; yields in any order, the engine's external sorter canonicalises order |
| `row_count()` | int | server-side `COUNT(*)` |
| `schema()` | list of column names | live introspection, not config echo |
| `close()` | — | release connections/cursors deterministically |

Target (`TargetAdapter`):

| Method | Returns | Notes |
|---|---|---|
| `read_table(table, columns, keys)` | DataFrame | `keys` = ORDER BY |
| `read_chunks(table, columns, keys, size)` | generator | bounded-memory verification |
| `query(sql)` | DataFrame | **must be read-only** — reject non-SELECT (SnowflakeTarget parses with sqlglot and only allows a single SELECT) |
| `row_count(table)` | int | |
| `schema()` / `schema(table)` | column spec | used by the schema stage |
| `primary_key(table)` | list | |
| `is_closed()` / `close()` | — | |

Targets also expose `.conn` (DB-API connection) and `.dialect`
(`"postgres"`, `"snowflake"`, …) which drives identifier quoting
(`libs/engine/schema.ident`), type maps and `information_schema` queries.

## Safety rules (non-negotiable)

- **Verification is read-only.** Source and target adapters open SELECT-only
  sessions. Writers are separate factories and are only invoked by the loader
  in explicit loader mode — never by `tests/migration/` (it is verify-only).
- **Credentials come from constructor args**, populated from `${ENV_VAR}`
  expansion in the env YAML or `.env`. Never log them, never accept them in
  the Excel workbook, never fall back to a default that could silently hit a
  real database.
- **Identifiers are vetted** (`[A-Za-z_]\w*`) and quoted per dialect.
  WHERE fragments pass through `where_is_safe` (single SELECT-safe fragment,
  no DDL/DML/comments/multi-statements).
- **Fail closed.** Unknown `type:` raises; a target that cannot introspect
  schema returns empty schema (validation then reports drift) rather than
  guessing.

## Snowflake specifics

- Real path: `snowflake-connector-python` (`account`/`user`/`password`/
  `warehouse`/`database`/`schema`/`role` from env yaml).
- Emulated path: `fake: true` + `fake_db_path` (a **directory** — fakesnow
  keeps one `.db` per database) runs `fakesnow` (DuckDB). One process-level
  `FakeSnow` instance is shared so loader and verifier connections see the
  same warehouse.
- Bulk loads use `write_pandas` (`snowflake.connector.pandas_tools`, or
  `fakesnow.pandas_tools` under emulation) — row-wise `executemany` is both
  slow and memory-heavy.
- **fakesnow limitations to know:** `information_schema` reports `TEXT` with
  no declared `character_maximum_length`, so VARCHAR-length checks are marked
  "not validated" under `fake: true` (still enforced on real Snowflake);
  constraint/DDL semantics differ from the real service. A green emulated
  run is *not* production sign-off.

## Checklist for a new database

1. `libs/adapters/<db>_source.py` / `<db>_target.py` implementing the tables
   above; register types.
2. Type map + aliases in `libs/engine/schema.py` (`<DB>_TYPE_MAP`,
   `TYPE_ALIASES[...]`) so contract types translate and introspected types
   map back.
3. `information_schema`/catalog queries in `libs/adapters/catalog.py` and
   `fetch_db_schema` / `fetch_primary_key` for that dialect.
4. Writer factory only if loader mode is in scope.
5. Env yaml block with `${ENV_VAR}` credential refs; document any emulator.
