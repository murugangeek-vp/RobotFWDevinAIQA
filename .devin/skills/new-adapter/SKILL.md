---
name: new-adapter
description: Add a source or target adapter via the registry — least-privilege, config-driven, no engine changes
argument-hint: "<adapter-name> <source|target> <technology>"
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
---

ADD A NEW ADAPTER

Open/closed: a new system plugs in via the registry — engine, keywords, and
suites must not change. Read `libs/adapters/base.py`, `registry.py`, and an
existing impl (`postgres_target.py` / `csv_source.py` /
`snowflake_target.py`) before writing.

## Source adapter (`libs/adapters/<name>_source.py`)

1. Subclass `SourceAdapter`; implement `read_batch()` → DataFrame,
   `row_count()`, `schema()` (ordered column names), `close()`.
2. Override `read_chunks(size)` if the backend can stream (server-side
   cursor / paginated API) — big source tables must not be read whole.
3. Decorate: `@register_source("<type-name>")`.

## Target adapter (`libs/adapters/<name>_target.py`)

1. Subclass `TargetAdapter`; implement `read_table`, `query(sql, params)`,
   `row_count`, `schema(table)` → `[(name, type, is_nullable), ...]`,
   `primary_key(table)`, `is_closed()`, `close()`.
2. Expose `.conn` (the DBAPI connection, READ-ONLY session — used by
   schema/PK introspection) and set `.dialect` to a key in
   `libs/engine/schema.py::TYPE_ALIASES` (extend that map if the dialect's
   type names differ).
3. `query()` is SELECT-only — verification depends on it; never allow
   mutation through this path.
4. Decorate: `@register_target("<type-name>")`.
5. Writer factory: `@register_writer("<type-name>")` on
   `fn(host, port, database, user, password)` returning a RW connection —
   separate from the RO path so verify-only needs no write credential.

## Wire-up

4. Add the import to `registry._ensure_builtin()` so the decorator runs.
5. Map the dialect's types in `schema.TYPE_ALIASES` if new (contract types:
   integer/string/decimal/date/timestamp/...).
6. Env YAML `target.type:` / `source.type:` = the registered name; secrets
   arrive as `${ENV_VAR}` refs expanded by `Load Environment` — the adapter
   receives `user`/`password` kwargs, never reads env itself.

## Validate

- `python -m unittest discover -s tests/pilot -p "test_*.py" -v`
- `robot -d results -v ENV_FILE:config/environments/<env-using-it>.yaml tests/pilot`
- Prove least privilege: the RO path cannot mutate (mirror
  `Read Only Role Cannot Mutate Target` in 90_negative_path.robot if the
  backend supports role enforcement).

## Rules

- Credentials only via ctor kwargs from env refs — adapter code never touches
  `os.environ` for secrets and never logs them.
- All SQL through bound params; identifiers vetted before use.
- A local emulation option (`fake:`) is welcome for dev (see fakesnow) but
  must never be the default.
