# TODO / Delivery Checklist — Data Reconciliation Testing Framework

Tick a box when the item's **acceptance check** passes. GitHub renders `- [x]` as a green tick.
Every stage ends with a **Stage gate** — do not start the next stage until its gate is ticked.

> **Scope:** two suites now exist — `tests/pilot/` (CSV → PostgreSQL,
> contract-driven) and `tests/migration/` (DB → DB, workbook-driven,
> verify-only). The bank-migration stream (Webster → Santander demo) has
> landed; remaining work is tracked in Stage 2 and the v1 roadmap below.

Progress: Stage 0 ▸ 13/14 · Stage 1 ▸ 8/9 · Stage 2 ▸ 3/6 · Stage 3 ▸ 0/4 · v1 roadmap: phases A–F

---

## Bank-migration stream — delivered

- [x] **MIG-01 Workbook-driven contracts** — `bank_mapping.xlsx` (Tables /
      FieldMap / Mapping sheets) compiles into validated contracts; the
      workbook is the single source of truth; fail-closed parsing.
- [x] **MIG-02 Verify-only `tests/migration/` suite** — 7 stages
      (mapping/source/dq/schema/recon/transform/negative) + e2e gate; no
      loader; RO roles on both sides.
- [x] **MIG-03 Postgres + Snowflake adapters** — source and target each;
      fakesnow emulation via `fake: true`; sqlglot single-SELECT vetting.
- [x] **MIG-04 Scale path** — server-side chunked reads + canonical external
      sort (temp SQLite) — flat RAM at million-row volumes; SHA-256 hash mode.
- [x] **MIG-05 Evidence** — `migration_summary.xlsx` (Run Info / Stage
      Results / Metadata / Diffs / Insights), `migration_report.md`,
      `run_summary.json`; `production_signoff` never granted by a run.
- [x] **MIG-06 Negative self-test** — exact missing/extra/tamper/duplicate
      detection on streamed copies; databases untouched.
- [x] **MIG-07 CI coverage** — `tests/migration` robot + unittest in the
      reconciliation workflow; emulated-target loader for demos/CI.

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

## Stage 2 — Production hardening

- [x] **PROD-01 Adapter architecture** — `SourceAdapter`/`TargetAdapter` ABCs +
      config-driven registry; CSV + PostgreSQL + Snowflake on it. ✅
- [x] **PROD-07 (partial) Least-privilege + secret scanning** — `recon_ro` SELECT-only
      proven by `Read Only Role Cannot Mutate Target`; writes rejected; gitleaks in CI. ✅
- [ ] **PROD-07 (rest) Central secret store** (Vault / cloud secret manager).
- [x] **PROD-08 (partial) Performance** — chunked reads + external canonical
      sort, bounded RAM, hash mode. ✅ _Rest: committed 1M-row benchmark gate._
- [ ] **PROD-09 Observability** — central run store, trend view, audit retention.
- [ ] **PROD-10 AI failure analysis + rollout** — failure clustering, assertion-diff check.

## v1 roadmap — general "any-bank" framework (megaplan phases)

- [x] **B — Snapshot & cutover (top priority).** `source.snapshot: exported`
      pins every postgres source session to one `pg_export_snapshot()` held
      open for the run; `source.snapshot_column` + `run.snapshot_id` is the
      bound watermark alternative (postgres + snowflake). Evidence carries a
      `cutover` block (`snapshot_id`, `source_snapshot`, `snapshot_enforced`,
      `target_asof`, `post_snapshot_arrivals`); `run.allow_post_snapshot`
      gates rows arriving after the watermark; non-fake targets refuse
      unenforced snapshots.
      _Rest: full DB-level seeded delta test needs a watermark column in the
      synthetic fixtures (detection + gate are unit-tested today)._
- [ ] **C — Financial controls & integrity.** Implement contract `controls`
      (count/sum/min/max/null_count/count_distinct + group_by) and
      `control_file` trailer compare — both are schema-declared today but
      never evaluated (fail-closed violation); `Relations` sheet for FK
      orphan checks across mapped tables.
- [ ] **D — Evidence polish.** `transform` stage compares transform columns
      only (today it duplicates `recon`); evidence validation requires
      `coverage == COMPLETE`; `run.fail_on_unmapped` gate; `pii` flag drives
      selective (not all-or-nothing) redaction; fuse DQ source passes.
- [ ] **E — Any-bank adapters & unified rules.** MySQL/Oracle/MSSQL adapters
      per `docs/ADAPTERS.md` checklist; S3/API sources (schema already
      reserves them); `libs/excelrules` extended to DB-backed sources so a
      client rules workbook runs in DB→DB migrations; "which workbook when"
      doc.
- [ ] **F — Observability & scale proof.** Run store + trend report;
      secret-store resolver in `_expand_env`; 1M-row benchmark gate in
      nightly CI; parallel mappings; retry/resume.

---

## Stage 3 — Cross-cutting / continuous

- [ ] **X-01** Keyword library stays reusable and documented; `find_keywords` before any new keyword.
- [ ] **X-02** New data contract published ⇒ tests added in the same sprint.
- [ ] **X-03** Test documentation maintained alongside code changes.
- [ ] **X-04** Quarterly review of read-only roles, MCP credentials, and secret rotation.
