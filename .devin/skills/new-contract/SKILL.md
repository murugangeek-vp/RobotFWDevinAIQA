---
name: new-contract
description: Onboard a new entity/feed — contract YAML, fixtures, environment wiring, same-sprint coverage
argument-hint: "<entity-name> <source-type> <target-table>"
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
---

ONBOARD A NEW FEED / ENTITY

Contract-driven design means a new feed is config + fixtures — the existing
suites apply unchanged (TODO X-02: coverage lands in the same sprint).

## Steps

1. **Contract.** Copy `config/contracts/customer.yaml` to
   `config/contracts/<entity>.yaml`. Set `contract_name`, `version: "1.0"`,
   `source` block (type/path/encoding/delimiter/header + ordered `columns:`),
   `target` (type/schema/table), `keys:` (stable business key(s)), and
   `columns:` entries with `type`, `nullable`, plus constraints as needed:
   `unique`, `max_length`, `regex`, `range`, `allowed_values`, `scale`,
   `date_format`, `ts_format`, `source_name`, `transform`.
   - Transforms: `"{col}"` templates + whitelisted funcs only
     (`reconcile._SAFE_FUNCS`). If you need a new function, add it there —
     deliberately — and say so in your summary.
   - Scoped migration? Add `source.row_filter` (see
     `customer_filtered.yaml`). Out-of-scope rows become `extra_in_target`
     if leaked.
   - If the contract needs a NEW KEY, extend
     `config/contracts/contract.schema.json` in the same change or the load
     fails closed.
2. **Fixtures.** Generate `data/samples/<entity>.csv` deterministically —
   extend `scripts/generate_samples.py` (or a new `generate_<entity>.py`),
   faker + fixed seed, synthetic banking values only. Add the corrupted
   siblings: `<entity>_bad_header.csv`, `<entity>_bad_dq.csv`,
   `<entity>_missing_rows.csv` with EXACT seeded defect counts.
3. **Environment.** Point an env YAML at it — copy
   `config/environments/dev.yaml`, set `source.contract:
   config/contracts/<entity>.yaml`. No credentials in YAML — `${VAR}` refs.
4. **Target table.** Ensure the target schema exists for the loader
   (non-prod) — column types must satisfy `schema.TYPE_ALIASES` for the
   dialect so 03_schema_validation passes.
5. **Validate.** Run:
   `robot -d results -v ENV_FILE:config/environments/<env>.yaml tests/pilot`
   → all green on the clean fixture; then negative checks with each corrupted
   sibling → FAILs with exact counts.
6. **Docs.** Add a section to docs/CONTRACTS.md (column mapping table +
   fixture list), following the customer.yaml entry.

## Rules

- No per-feed test code — if the suites can't express a check, the check
  belongs in the contract/engine, not a bespoke .robot file.
- Choose real business keys; never row numbers.
- Constraints on derived columns are checked post-transform — a malformed
  input must not hide behind a transform.
