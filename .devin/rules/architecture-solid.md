---
description: "Clean architecture, SOLID, and design-pattern rules for libs/ and scripts/"
trigger: glob
globs: "libs/**/*.py,scripts/**/*.py"
---

# Architecture & Design Rules

## Layer boundaries (dependency direction — do not invert)

```
tests/*.robot ──► libs/robot ──► libs/engine ◄── libs/adapters (I/O only)
                        └────► libs/excelrules, libs/mapping
```

- `libs/engine/` is PURE: pandas in, models out. No DB connections, no file
  I/O, no `robot.*` imports, no `os.environ` reads for connections.
- `libs/adapters/` owns ALL source/target I/O. Engine and suites never touch
  psycopg2/snowflake/openpyxl directly.
- `libs/robot/ReconciliationLibrary.py` is a THIN keyword facade — it
  delegates to engine/adapters/excelrules. New comparison logic goes in the
  engine, not in keywords.
- `libs/loader.py` owns fixture loading (`recon_rw`) — keep it the only write
  path.

## SOLID applied here

- **Open/closed:** add a source/target = new adapter class +
  `@register_source("type")` / `@register_target("type")` /
  `@register_writer("type")` + one import line in `registry._ensure_builtin`.
  Never edit engine or suites to add an adapter.
- **Single responsibility:** `reconcile` (compare), `rules` (data quality),
  `schema` (contract + target schema validation), `models` (dataclasses),
  `excelrules` (workbook rules), `mapping` (migration workbook). Put new
  logic in the module that owns the concern.
- **Liskov:** adapters must honor the ABC contracts — `read_chunks` yields
  ≤size DataFrames ordered for merge-compare; `TargetAdapter` exposes `.conn`
  (read-only session) and `.dialect` (key in `schema.TYPE_ALIASES`).
- **Interface segregation:** verification needs only `TargetAdapter`
  read methods; writers are separate factories so RO paths can't write.
- **Dependency inversion:** engine consumes adapter DataFrames, not concrete
  adapters — suites stay adapter-agnostic via env YAML `type:`.

## Correctness & failure semantics

- Fail closed with a message naming the contract element (rule id, column,
  table, env var) — never silent-skip, never default-to-pass.
- Exactness over convenience: report precise counts and key samples; a
  reconciliation tool that approximates is worthless.
- Normalization (type coercion, decimal scale, date formats) lives in ONE
  place in the engine — don't re-implement per keyword or per adapter.
- Determinism: comparison results must not depend on row order, dict order,
  or wall-clock — sort keys, bound samples.
- Contract validation happens at load against `contract.schema.json`; extend
  the schema JSON when adding contract keys, or new keys fail closed.

## Code style

- Python 3.11+, type hints, mypy-clean under `libs/` (pre-commit enforced).
- ruff: line-length 100, rules E/F/W/I/B/UP/SIM; `ruff-format` is the
  formatter — don't hand-format.
- Compact code, no speculative abstractions; comments only where the codebase
  already uses them (docstrings on public functions are the norm here).
- New dependencies: pin versions in requirements.txt, prefer ≥7-day-old
  releases, justify heavyweight deps for a test tool.
- Engine unit tests live beside suites as `tests/pilot/test_*.py`
  (unittest discovery) — add coverage when changing engine logic.
