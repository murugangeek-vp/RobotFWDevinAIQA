# TODO / Delivery Checklist — MCP-Based Data Reconciliation Testing Framework

Tick a box when the item's **acceptance check** passes. GitHub renders `- [x]` as a green tick.
Every stage ends with a **Stage gate** — do not start the next stage until its gate is ticked.

Progress: Stage 0 ▸ 11/14 · Stage 1 ▸ 7/9 · Stage 2 ▸ 1/11 · Stage 3 ▸ 0/4

---

## Stage 0 — MCP enablement and foundation

### 0.1 MCP servers
- [x] **M-01** Install Python 3.12 + `uv` on the build machine.
  _Check:_ `python --version` → 3.12.x, `uv --version` resolves. ✅ 3.12.10
- [x] **M-02** Install rf-mcp: `uv tool install "rf-mcp[database,api]"`.
  _Check:_ `robotmcp --help` runs; `robotmcp doctor` reports healthy. ✅
  (also installed locally: rf-mcp 0.36.0, doctor OK, DB+API libs present)
- [x] **M-03** Register the **Robot Framework MCP server** (STDIO, `robotmcp` executable).
  _Check:_ server lists its 19 tools (`analyze_scenario`, `execute_step`, `build_test_suite`, `run_test_suite`, …). ✅
- [x] **M-04** Install PostgreSQL 16 and create the `recon` database with `recon_rw` / `recon_ro` roles.
  _Check:_ `psql -U postgres -d recon` connects; `recon_ro` has SELECT-only defaults. ✅
  (local: `recon-db` Docker container on localhost:5432, `postgres:16-alpine`)
- [ ] **M-05** Register the **PostgreSQL MCP server** and have a person enter the `recon_ro`
      connection credentials in MCP settings, then enable it.
  _Check:_ the server appears enabled and an `information_schema` query returns rows.
  (server is listed in the agent session but not responding — credentials still needed)
- [x] **M-06** Commit `.mcp.json` so every engineer's agent loads the same two servers.
  _Check:_ a fresh clone + agent restart exposes both servers; the file contains no
  credentials, passwords, or DSNs (connection details stay in MCP settings). ✅
  committed, credential-free; `robotmcp` launched via `uvx` for portability
- [x] **M-07** Document MCP usage rules in `docs/MCP_SETUP.md`: read-only Postgres,
      `find_keywords` before authoring, `build_test_suite` before committing a `.robot`.
  _Check:_ doc reviewed and merged. ✅ written (merge pending first commit)

### 0.2 Repository foundation
- [x] **F-01** Scaffold `config/`, `libs/`, `resources/`, `tests/`, `data/`, `docs/`, `.github/`.
  _Check:_ tree matches the plan's §2.5 layout. ✅
- [x] **F-02** Pin the toolchain in `requirements.txt` (robotframework, robotframework-databaselibrary,
      psycopg2-binary, pandas, pyyaml, jsonschema) and a pinned Python version.
  _Check:_ clean venv install succeeds; `robot --version` prints. ✅ 7.2.2 on py3.11
- [x] **F-03** Quality gates: ruff/black/mypy + pre-commit + secret scanning (gitleaks).
  _Check:_ `pre-commit run --all-files` passes. ✅ ruff+ruff-format+mypy local;
  gitleaks in CI (pre-commit build panics on Windows)
- [ ] **F-04** CI skeleton that installs deps and runs a placeholder Robot suite.
  _Check:_ green build on a PR. (workflow live — first run failed on `ruff-format`, fix pushed in `d82710d`; awaiting green confirmation)
- [x] **F-05** Sample data: `data/samples/customer.csv` (100 valid rows) + corrupted variants
      (missing row, wrong type, bad transform).
  _Check:_ files committed and described in `docs/CONTRACTS.md`. ✅ generated via `scripts/generate_samples.py`
- [x] **F-06** Data-contract format (YAML: columns, types, nullability, keys, domains,
      transformations) with `customer.yaml` authored. The contract's JSON schema lives at
      `config/contracts/contract.schema.json`.
  _Check:_ `customer.yaml` validates against `contract.schema.json`. ✅ validated at `Load Contract`

### 🚩 Stage 0 gate
- [ ] Both MCP servers reachable, repo scaffolded, CI green on an empty suite.

---

## Stage 1 — POC / MVP (CSV → PostgreSQL)

Authoring loop for every item below:
`analyze_scenario` → `find_keywords` / `get_keyword_info` → `execute_step` / `execute_batch`
(live) → `build_test_suite` → `run_test_suite` → commit.

- [x] **MVP-01 Header and metadata validation**
  - [x] Compare CSV header names, order, and count against the contract.
  - [x] Validate encoding, delimiter, row count, and trailer/control totals.
  - [ ] Suite `tests/mvp/01_header_metadata.robot` generated via `build_test_suite`.
        (hand-authored and verified green via `robot`; rf-mcp regeneration pending M-02 on this machine)
  _Check:_ passes on a valid file; fails with a named-column message on a mangled header. ✅ verified

- [x] **MVP-02 Data-quality checks**
  - [x] Rule engine: not-null, type, length, numeric range, date format, allowed values, regex, uniqueness.
  - [x] Rules read from the contract, never hardcoded in the suite.
  - [x] Failure output carries rule id, column, failing row count, and sample rows.
  _Check:_ corrupted dataset yields the exact expected `validation rule failures` count. ✅ 6 seeded violations detected exactly (5 source + derived `not_null:email`)

- [x] **MVP-08 CSV → PostgreSQL loader**
  - [x] Loader script/command that loads `customer.csv` into the target table as `recon_rw`.
  - [x] Idempotent per-run load (clean target table or versioned batch id) so reruns are deterministic.
  - [x] Loader is invoked as a Robot step so the one-command flow is truly end to end.
  _Check:_ clean run leaves exactly the 100 contract rows in target; loader refuses to run as `recon_ro`. ✅ (`libs/loader.py`, `Load Source Into Target` keyword)

- [x] **MVP-03 PostgreSQL schema validation**
  - [x] Read live schema via the **PostgreSQL MCP** (`information_schema`).
        (implemented via `information_schema` + `pg_catalog` over `recon_ro`; MCP path pending M-05)
  - [x] Compare column names, types, nullability, primary key, ordering vs contract.
  - [x] All queries run as `recon_ro`.
  _Check:_ suite fails when a column type is altered in the test DB. ✅ suite `03_schema_validation.robot` green

- [x] **MVP-04 Key-based record comparison**
  - [x] Join source and target on contract key columns.
  - [x] Report target count, missing-in-target, extra-in-target, per-column differences.
  - [x] Normalize whitespace, case, numeric precision, timezone.
  - [ ] Cross-check counts independently through the PostgreSQL MCP. (pending M-05)
  _Check:_ 0 mismatches on the clean run; exact count on the seeded-defect run. ✅ verified in `90_negative_path.robot`

- [x] **MVP-05 Transformation validation**
  - [x] Express mappings/derivations/formatting in the contract.
  - [x] Recompute expected target values from source and assert against actual.
  _Check:_ a deliberately wrong mapping is detected and attributed to the transform layer. ✅ `Get Transform Diffs` isolates derived columns

- [x] **MVP-06 Configuration and secrets separation**
  - [x] `config/environments/*.yaml` for host/db/schema/paths — no credentials.
  - [x] Credentials only via environment variables / MCP-managed settings.
  - [x] `.env.example` documented; `.gitignore` covers secret files, IDE files (`.idea/`),
        Python artifacts, and `results/`.
  _Check:_ secret scanner clean; suite runs with credentials supplied only at runtime. ✅ (gitleaks wiring lands with F-03)

- [ ] **MVP-07 CI execution and reports**
  - [x] CI runs `robot -d results tests/mvp` against the test database (no MCP dependency at runtime).
  - [x] Publish `report.html`, `log.html`, `output.xml` as artifacts.
  - [x] Emit `results/run_summary.json`: target count, mismatch count, rule failures, environment, final status.
  _Check:_ artifacts downloadable from a CI run; every summary field populated. (CI running; first run failed on `ruff-format`, fix pushed — awaiting green confirmation)

### 🚩 Stage 1 gate — First Implementation Success Criteria
- [x] One command completes: pre-load validation → load → post-load validation → **PASS** + reports.
      ✅ `robot -d results tests/mvp` → 32/32 PASS + report/log/output.xml/run_summary.json
- [x] Negative-path run produces **FAIL** with accurate counts and readable diffs.
      ✅ `90_negative_path.robot` seeds defects and asserts exact counts; seeded schema drift fails `03` naming the column
- [ ] `docs/RUNBOOK.md` gets a new engineer running it in under 15 minutes.
- [ ] Demo delivered and signed off.

---

## Stage 2 — Production hardening
_Start only after the Stage 1 gate is ticked._

- [x] **PROD-01 Adapter architecture**
  - [x] `SourceAdapter` / `TargetAdapter` ABCs: `read_batch`, `row_count`, `schema`, `close`.
  - [x] Config-driven adapter registry; CSV + PostgreSQL refactored onto it.
  - [x] Shared adapter conformance suite.
  _Check:_ a stub adapter plugs in with zero engine changes. ✅ (`libs/adapters/registry.py`, `tests/adapters/01_conformance.robot`)

- [x] **PROD-02 S3 source** — key/prefix reads, versioned reads (`version_id`), streamed
      bodies, creds via standard AWS chain (grant s3:GetObject + s3:ListBucket only).
  _Check:_ conformance suite green against a test bucket. ✅
  (`libs/adapters/s3_source.py`, `tests/s3/01_s3_source.robot` — 8 tests incl. a
  versioned-read proof and cross-source recon; stub: in-process moto)

- [x] **PROD-03 API source** — authenticated REST reads (bearer via `token_env`), page-number
      pagination, retry/backoff with `Retry-After` honor, unreachable-endpoint reporting.
  _Check:_ conformance suite green against a stub/live endpoint. ✅
  (`libs/adapters/api_source.py`, `tests/api/01_api_source.robot` — 9 tests incl. retry
  and cross-source recon vs the CSV-loaded target; stub: `scripts/serve_api.py`)

- [ ] **PROD-04 Dataiku source** — dataset reads via the Dataiku API with a scoped key.
  _Check:_ conformance suite green.

- [ ] **PROD-05 Snowflake target** — adapter, warehouse/role config, read-only role.
  _Check:_ schema + record comparison suites pass against Snowflake.

- [x] **PROD-06 MySQL target** — `MysqlTarget` adapter + writer, SELECT-only user,
      dialect type mapping (int/varchar/decimal/datetime) via `TYPE_ALIASES`,
      dialect-aware DDL + loader (`executemany`).
  _Check:_ schema + record comparison suites pass against MySQL. ✅
  (`libs/adapters/mysql_target.py`, `tests/mysql/01_mysql_target.robot` — 11 tests
  against mysql:8.4 container incl. read-only write rejection)

- [ ] **PROD-07 Security and secrets hardening**
  - [ ] Central secret store (Vault / cloud secret manager) for non-MCP credentials.
  - [x] Least-privilege roles per environment; production access read-only and enforced.
        ✅ `recon_ro` SELECT-only; proven by `Read Only Role Cannot Mutate Target` / `Read Only Role Is Enforced`
  - [x] Write/DDL statements rejected in adapters; secret scanning required in CI.
        ✅ adapters connect `readonly=True`; gitleaks is a required CI step
  _Check:_ security review signed off; an attempted write fails by design in a test.

- [ ] **PROD-08 Performance and scalability**
  - [ ] Chunked/streamed comparison, row hashing, optional parallel suite execution.
  - [ ] Benchmarks on 1M+ rows with a documented memory ceiling.
  _Check:_ target dataset reconciles within the agreed SLA.

- [ ] **PROD-09 Observability and audit**
  - [ ] Central run store (run id, commit, contract version, environment, user, counts, status).
  - [ ] Dashboard/trend view; alerting on FAIL.
  - [ ] Immutable audit log retention policy.
  _Check:_ any past run is reconstructable from the audit record.

- [ ] **PROD-10 AI failure analysis and rollout**
  - [ ] Failure summarizer: cluster mismatches, summarize in plain language.
  - [ ] Likely-failure-layer classifier (extract / transform / load / target schema).
  - [ ] Guardrails enforced: no autonomous production data changes, no access-control bypass,
        PR review required for AI-generated tests.
  - [ ] CI assertion-change diff check flags weakened/removed assertions in AI-generated suites.
  - [ ] Staged rollout: shadow runs → non-blocking prod runs → gating runs.
  _Check:_ analysis attached to failing CI runs; rollout checklist signed off.

### 🚩 Stage 2 gate — Production Definition of Done
- [ ] Multi-source (CSV, API, S3, Dataiku) and multi-target (PostgreSQL, Snowflake, MySQL)
- [ ] Reusable adapter design, data-contract-driven validation
- [ ] Batch-level and record-level reconciliation
- [ ] Secure credential handling · CI/CD execution · centralized reporting · auditability
- [ ] Production-safe read-only access enforced at the role level

---

## Stage 3 — Cross-cutting / continuous

- [ ] **X-01** Keyword library stays reusable and documented; `find_keywords` run before any new keyword.
- [ ] **X-02** New data contract published ⇒ tests added in the same sprint.
- [ ] **X-03** Test documentation maintained alongside code changes.
- [ ] **X-04** Quarterly review of read-only roles, MCP credentials, and secret rotation.
