---
description: "Data-contract and environment YAML authoring rules"
trigger: glob
globs: "config/**/*.yaml,config/**/*.json"
---

# Contracts & Environment Config

## Data contracts (`config/contracts/*.yaml`)

- Every contract validates against `config/contracts/contract.schema.json` at
  `Load Contract`. Adding a new contract KEY means extending the schema JSON
  in the same change — unknown keys fail closed.
- The contract is the single source of truth: source shape, target columns,
  types, nullability, keys, domains (range/allowed_values/regex), transforms.
  Nothing about a feed's fields may live in test code or keywords.
- `keys:` defines record identity for comparison — composite keys are a list;
  choose stable business keys (account id, customer id), never row numbers.
- Transforms use `"{col}"` templates plus whitelisted functions
  (`lower/upper/round/strip/map/trunc_words/date_fmt/ifnull/...` — see
  `libs/engine/reconcile.py::_SAFE_FUNCS`). New functions are added there
  deliberately and reviewed — arbitrary code is never evaluated.
- `source.row_filter` declares migration scope (e.g. active accounts only).
  Out-of-scope rows are excluded from expected AND flagged `extra_in_target`
  if the ETL leaks them. Use `allow_empty: true` only when a zero-row scope is
  genuinely valid; filters on unknown columns fail at load.
- `metadata.expected_row_count` is ONLY for feeds with a contractual fixed
  size — normally the expected count is dynamic (rows the source delivered).
- Bump `version:` on semantic changes — audit reports record it.

## Environment configs (`config/environments/*.yaml`)

- One file per environment; passed via `robot -v ENV_FILE:...`.
- `target.type` / `source.type` must be a registered adapter type — the
  registry fails closed on unknown names.
- NO credentials: connection fields only; secrets are `${ENV_VAR}` whole-value
  references (`user: ${RECON_RO_USER}`). The expander fails closed on missing
  or empty vars — do not add literal fallbacks.
- `fake: true` / `fake_db_path` (fakesnow) are for local emulation only —
  never enable against a real endpoint, and keep fake targets out of anything
  labeled production.
- Source credentials for migration envs use RO roles — a migration
  verification environment must not be able to write to the SOURCE bank.

## Secrets separation (Pilot-06)

Config files are committed; credentials never are. If you find yourself typing
a password, account, or DSN into YAML/JSON/Python — stop and use `${VAR}` +
`.env` instead.
