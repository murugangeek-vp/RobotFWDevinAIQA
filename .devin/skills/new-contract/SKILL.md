---
name: new-contract
description: Onboard a new entity/table mapping — workbook rows, fixtures, environment wiring, same-sprint coverage
argument-hint: "<entity-name> <hop> <source-table> <target-table>"
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
---

ONBOARD A NEW ENTITY / TABLE MAPPING

Workbook-driven design means a new feed is workbook rows + fixtures — the
existing `tests/migration` suites apply unchanged. No per-feed test code.

## Steps

1. **Workbook mapping.** Add rows to the hop's workbook
   (`data/mapping/phase1_hop*.xlsx`, format in docs/EXCEL_MAPPING.md):
   - `Tables`: Mapping ID, Source Schema/Table, Target Schema/Table, Key
     Columns, optional Where Clause / Recon Mode / Batch Size, Enabled=yes.
   - `FieldMap`: one row per target column — Source Column, Target Column,
     Target Type, Nullable, optional Transformation.
   - `Controls`: certified aggregates to validate against the trailer.
   - `Relations`: parent/child FK pairs for orphan detection.
   - Transforms: engine expressions (`ifnull`, `casewhen`, `concat`,
     `lpad`, `regexp_match`, `literal`, …) — sandboxed whitelist only. If a
     client spec gives raw SQL, it goes through
     `libs/mapping/sql_transform.py` which fails closed on unsupported
     constructs. A new safe function is a deliberate `_SAFE_FUNCS` change —
     say so in your summary.
   - Regenerate from a spec sheet via `scripts/generate_phase1_workbooks.py`
     when the spec is the source.
2. **Fixtures.** Extend `scripts/generate_phase1_csv.py` — deterministic
   synthetic rows only, plus corrupted siblings under
   `data/phase2/negative/` with EXACT seeded defect counts and matching
   trailer totals (`name,group,value`).
3. **Environment.** Env YAMLs under `config/environments/phase1_*.yaml`
   pair one-to-one with workbooks; copy the hop's env, adjust
   `source.dir`/tables/`control_file`. Credentials stay `${VAR}` refs.
4. **Target table.** Ensure the target schema/table exists for the loader
   (`allow_qa_load` demo envs only) — column types must satisfy
   `schema.TYPE_ALIASES` so stage 03 passes.
5. **Validate.** Run the hop suite:
   `robot -d results/phase1/<hop> -v MIGRATION_ENV:<env>.yaml -v MAPPING_WORKBOOK:<wb>.xlsx tests/migration`
   → all green on the clean fixture; then the negative env → FAILs with
   exact counts.
6. **Spec gaps.** Anything the spec leaves ambiguous must be resolved
   explicitly and documented on the workbook README sheet — never silently
   dropped (see PARTY_TYPE → `literal('PENDING_SPEC')` precedent).

## Rules

- If the suites can't express a check, the check belongs in the
  contract/engine, not a bespoke .robot file.
- Keys are **target** column names; choose real business keys, never row
  numbers.
- Constraints on derived columns are checked post-transform — a malformed
  input must not hide behind a transform.
