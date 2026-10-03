# TODO / Delivery Checklist — Data Reconciliation Testing Framework (Pilot)

Tick a box when the item's **acceptance check** passes. GitHub renders `- [x]` as a green tick.
Every stage ends with a **Stage gate** — do not start the next stage until its gate is ticked.

> **Branch scope:** this checklist covers the pilot branch — CSV source →
> PostgreSQL target. The multi-source / multi-target / bank-migration work
> stream lives on the main development branch (`devin/1790991363-bank-migration-recon`).

Progress: Stage 0 ▸ 13/14 · Stage 1 ▸ 8/9 · Stage 2 (pilot scope) ▸ 2/5 · Stage 3 ▸ 0/4

---

## Stage 0 — MCP enablement and foundation

### 0.1 MCP servers
- [x] **M-01** Install Python 3.12 + `uv` on the build machine.
  _Check:_ `python --version` → 3.12.x, `uv --version` resolves. ✅ 3.12.10
- [x] **M-02** Install rf-mcp: `uv tool install "rf-mcp[database,api]"`.
  _Check:_ `robotmcp --help` runs; `robotmcp doctor` reports healthy. ✅
- [x] **M-03** Register the **Robot Framework MCP server** (STDIO, `robotmcp` executable).
  _Check:_ server lists its 19 tools (`analyze_scenario`, `execute_step`, `build_test_suite`, `run_test_suite`, …). ✅
- [x] **M-04** Install PostgreSQL 16 and create the `recon` database with `recon_rw` / `recon_ro` roles.
  _Check:_ `psql -U postgres -d recon` connects; `recon_ro` has SELECT-only defaults. ✅
  (local: `recon-db` Docker container on localhost:5432, `postgres:16-alpine`)
- [ ] **M-05** Register the **PostgreSQL MCP server** and have a person enter the `recon_ro`
      connection credentials in MCP settings, then enable it.
  _Check:_ the server appears enabled and an `information_schema` query returns rows.
- [x] **M-06** Commit `.mcp.json` so every engineer's agent loads the same two servers.
  _Check:_ a fresh clone + agent restart exposes both servers; the file contains no
  credentials, passwords, or DSNs (connection details stay in MCP settings). ✅

### 0.2 Repository foundation
- [x] **F-01** Scaffold `config/`, `libs/`, `resources/`, `tests/`, `data/`, `docs/`, `.github/`. ✅
- [x] **F-02** Pin the toolchain in `requirements.txt` (robotframework, psycopg2-binary,
      pandas, pyyaml, jsonschema) and a pinned Python version.
  _Check:_ clean venv install succeeds; `robot --version` prints. ✅ 7.2.2 on py3.11
- [x] **F-03** Quality gates: ruff/ruff-format/mypy + pre-commit + secret scanning (gitleaks).
  _Check:_ `pre-commit run --all-files` passes. ✅ gitleaks runs in CI
- [x] **F-04** CI skeleton that installs deps, provisions Postgres, runs the suites, and
      publishes artifacts — green on PRs, pushes, nightly, and manual dispatch
      (suite/test/tag selectors). ✅ `.github/workflows/reconciliation.yml`
- [x] **F-05** Sample data: `data/samples/` fixtures (customer, account extract + codes,
      address) + corrupted variants, all generated deterministically.
  _Check:_ `scripts/generate_samples.py` ✅
- [x] **F-06** Data-contract format (YAML: columns, types, nullability, keys, domains,
      transformations, joins, row_filter) with `contract.schema.json` validation.
  _Check:_ every contract validates at `Load Contract`. ✅

### 🚩 Stage 0 gate
- [ ] Both MCP servers reachable, repo scaffolded, CI green on an empty suite.

---

## Stage 1 — Pilot (CSV → PostgreSQL)

- [x] **Pilot-01 Header and metadata validation** — CSV header names/order/count,
      encoding, delimiter, row count vs contract; named-column failures. ✅
- [x] **Pilot-02 Data-quality checks** — 8 rule types contract-driven (not-null, type,
      length, range, allowed values, regex, unique, transform-input); exact failure
      counts with rule ids and samples. ✅ 6 seeded violations detected exactly
- [x] **Pilot-03 PostgreSQL schema validation** — live `information_schema`/`pg_catalog`
      vs contract as `recon_ro`, split by aspect (columns, types, nullability, PK). ✅
- [x] **Pilot-04 Key-based record comparison** — count, missing/extra keys, per-column
      diffs with normalization; plus `row_filter` migration scope (in-scope only,
      leaked rows = extras). ✅
- [x] **Pilot-05 Transformation validation** — templates, lower/upper/round/strip,
      `map()` code tables, multi-source joins (`SN|11000`), `trunc_words()` word
      boundary; diffs attributed to the transform layer. ✅ 32 tests in one suite
- [x] **Pilot-06 Configuration and secrets separation** — env YAML without credentials;
      env vars / `.env` (git-ignored) only; gitleaks in CI. ✅
- [x] **Pilot-07 CI execution and reports** — `report.html`/`log.html`/`output.xml`/
      `run_summary.json` artifacts; manual dispatch for single-test runs. ✅
- [x] **Pilot-08 CSV → PostgreSQL loader** — idempotent TRUNCATE+INSERT as `recon_rw`;
      refuses read-only sessions; **`RECON_SKIP_LOAD` verify-only mode** leaves the
      target untouched and needs no write credential. ✅
- [ ] **Pilot-09** `docs/RUNBOOK.md` gets a new engineer running it in under 15 minutes.

### 🚩 Stage 1 gate — First Implementation Success Criteria
- [x] One command completes: pre-load validation → load → post-load validation → **PASS** + reports.
      ✅ `robot -d results tests/pilot` → 72/72 PASS on clean fixtures
- [x] Negative-path run produces **FAIL** with accurate counts and readable diffs.
      ✅ `90_negative_path.robot` seeds defects and asserts exact counts; verify-only
      runs detect pre-seeded tampering without reloading
- [ ] Demo delivered and signed off.

---

## Stage 2 — Production hardening (pilot scope)

- [x] **PROD-01 Adapter architecture** — `SourceAdapter`/`TargetAdapter` ABCs +
      config-driven registry; CSV + PostgreSQL on it. ✅
- [x] **PROD-07 (partial) Least-privilege + secret scanning** — `recon_ro` SELECT-only
      proven by `Read Only Role Cannot Mutate Target`; writes rejected; gitleaks in CI. ✅
- [ ] **PROD-07 (rest) Central secret store** (Vault / cloud secret manager).
- [ ] **PROD-08 Performance** — chunked/streamed comparison, 1M-row benchmark ceiling.
- [ ] **PROD-09 Observability** — central run store, trend view, audit retention.
- [ ] **PROD-10 AI failure analysis + rollout** — failure clustering, assertion-diff check.

_Out of scope on this branch: additional source adapters (S3/REST/Dataiku),
additional targets (Snowflake/MySQL), and the Webster→Santander migration
tooling — they continue on the main development branch._

---

## Stage 3 — Cross-cutting / continuous

- [ ] **X-01** Keyword library stays reusable and documented; `find_keywords` before any new keyword.
- [ ] **X-02** New data contract published ⇒ tests added in the same sprint.
- [ ] **X-03** Test documentation maintained alongside code changes.
- [ ] **X-04** Quarterly review of read-only roles, MCP credentials, and secret rotation.
