# Enterprise Data Reconciliation Testing Framework — MCP-Based Implementation Plan

Version: 2.0 (MCP-based)
Owner: QA Engineering / Data Platform
Automation stack: Robot Framework + Python, driven through MCP servers
Status: POC environment provisioned and verified

> **Branch scope (`pilot`):** this branch ships only Phase 1 — CSV source →
> PostgreSQL target. Phase-2 items (S3/API/Dataiku sources, Snowflake/MySQL
> targets, migration tooling) are the roadmap and live on the main development
> branch, not in this codebase.

---

## 1. Purpose

Build a reusable, data-contract-driven test framework that validates data moving from a
source system into a target database, and proves correctness automatically — no manual
spreadsheet comparison.

Every run reports five facts:

| Metric | Meaning |
| --- | --- |
| Target count | Number of records loaded into the target |
| Mismatch count | Records that differ between source and target |
| Validation rule failures | Data-contract / data-quality rule violations |
| Environment | Which environment the run executed against (dev / test / prod-read) |
| Final status | PASS or FAIL for the whole reconciliation run |

---

## 2. MCP Server Architecture

The framework is built and operated through MCP servers rather than ad-hoc scripting.
Devin authors and runs Robot Framework through the Robot Framework MCP server, and
inspects/validates the target database through the PostgreSQL MCP server.

### 2.1 Servers in use

| MCP server | Slug (installed) | Transport | Role in this project |
| --- | --- | --- | --- |
| Robot Framework (`rf-mcp` / RobotMCP) | `robotframework-mcp-cf36` | STDIO | Keyword discovery, live step execution, suite generation, suite execution |
| PostgreSQL | `cognition-postgres-cf32` | STDIO (docker) | Target schema introspection, read-only verification queries |

Installed on the session machine and verified:

```bash
# Robot Framework MCP server
uv tool install "rf-mcp[database,api]"     # provides robotmcp / rf-mcp executables
# STDIO command registered: C:/Users/Administrator/.local/bin/robotmcp.exe

# PostgreSQL 16 (POC target database)
choco install -y postgresql16 --params '/Password:<local-dev-password>'
```

### 2.2 Robot Framework MCP (`rf-mcp`) — tools and how we use them

| Tool | Use in this project |
| --- | --- |
| `recommend_libraries`, `check_library_availability` | Confirm DatabaseLibrary / RequestsLibrary / OperatingSystem are present before authoring |
| `find_keywords`, `get_keyword_info` | Reuse **approved existing keywords**; never invent a keyword that already exists |
| `analyze_scenario` | Turn a data contract / acceptance criterion into a concrete step plan |
| `manage_session`, `set_library_search_order` | One rf-mcp session per reconciliation scenario, with a deterministic library order |
| `execute_step`, `execute_batch`, `execute_flow`, `resume_batch` | Run steps **live** against CSV + PostgreSQL so locators/queries are proven, not guessed |
| `get_session_state` | Inspect variables and intermediate result sets while debugging a mismatch |
| `build_test_suite` | Emit the `.robot` suite from steps that actually passed |
| `run_test_suite` | Execute the suite and produce `output.xml` / `log.html` / `report.html` |

**Rule:** a `.robot` file is only committed after its steps passed live via `execute_step`
/ `execute_batch` and the suite was emitted by `build_test_suite`. No hand-written,
never-executed suites.

### 2.3 PostgreSQL MCP — how we use it

- Introspect `information_schema` for Pilot-03 schema validation (columns, types,
  nullability, primary keys).
- Run read-only verification queries (counts, key sets, aggregates) independently of the
  Robot suite, as a second opinion when a mismatch is being triaged.
- **Read-only by policy**: it connects with the `recon_ro` role. It must never be used to
  insert, update, delete, or DDL against a production-like target.

Credentials for the PostgreSQL MCP installation are entered by a person in
Settings → MCP marketplace → the installation's configure page. They are never pasted
into session text or committed.

### 2.4 Layered design

```
        ┌──────────────────────────┐
        │ Data contract (YAML)     │
        └────────────┬─────────────┘
                     │
   ┌─────────────────▼──────────────────┐
   │ rf-mcp  (Robot Framework MCP)      │
   │  analyze → find_keywords →         │
   │  execute_step/batch (live) →       │
   │  build_test_suite → run_test_suite │
   └───────┬─────────────────┬──────────┘
           │                 │
 ┌─────────▼────────┐  ┌─────▼──────────────────┐
 │ Source adapter   │  │ Target adapter         │
 │ CSV (POC)        │  │ PostgreSQL (POC)       │
 │ → S3/API/Dataiku │  │ → Snowflake/MySQL      │
 └─────────┬────────┘  └─────┬──────────────────┘
           │                 │
   ┌───────▼─────────────────▼───────┐    ┌──────────────────────┐
   │ Reconciliation engine (Python)  │    │ PostgreSQL MCP       │
   │ counts · keys · rules · diff    │◀───│ read-only introspect │
   └───────────────┬─────────────────┘    └──────────────────────┘
                   │
   ┌───────────────▼─────────────────┐
   │ Robot reports + run_summary.json│
   │ + audit record                  │
   └─────────────────────────────────┘
```

### 2.5 Proposed repository layout

```
data-reconciliation-testing/
├── .mcp.json                        # rf-mcp + postgres MCP config for local agents
├── config/
│   ├── environments/dev.yaml
│   └── contracts/{customer.yaml,contract.schema.json}
├── libs/
│   ├── adapters/{base.py,csv_source.py,postgres_target.py,registry.py}
│   ├── engine/{reconcile.py,rules.py,schema.py,models.py}
│   ├── loader.py
│   └── robot/ReconciliationLibrary.py
├── resources/keywords/{common.resource,reconciliation.resource}
├── tests/pilot/
├── data/samples/customer.csv
├── docs/{IMPLEMENTATION_PLAN.md,TODO.md,CONTRACTS.md,RUNBOOK.md,QA_GUIDE.md}
└── .github/workflows/reconciliation.yml
```

---

## 3. AI (Devin) Responsibilities and Guardrails

Devin **is used to**:

1. Generate and modify Robot Framework tests — via `rf-mcp` (`analyze_scenario` →
   `execute_step` → `build_test_suite`), not by hand-writing unverified `.robot` files.
2. Reuse approved keywords — always `find_keywords` / `get_keyword_info` before authoring
   a new keyword.
3. Add tests from new data contracts when a contract is published.
4. Analyze test failures from `output.xml` and rf-mcp session state.
5. Summarize mismatches (counts, affected columns, representative sample rows).
6. Propose the likely failure layer: source extract / transform / load / target schema.
7. Maintain test documentation.

Devin **must not**:

1. Autonomously modify production data. The PostgreSQL MCP connection is read-only.
2. Bypass or weaken access controls, credential handling, or approval gates.
3. Commit secrets or connection strings; MCP credentials live in MCP settings only.
4. Change expected results to force a green run.

All AI-generated changes land through pull requests with human review.

---

## 4. Environment Provisioned (verified this session)

| Component | Status | Detail |
| --- | --- | --- |
| Python | ✅ | 3.12.10 (`C:\Python312`) |
| uv | ✅ | installed via pip |
| rf-mcp | ✅ | `rf-mcp[database,api]`; 19 MCP tools listed successfully |
| Robot Framework MCP server | ✅ | registered, STDIO, tools reachable from the session |
| PostgreSQL 16 | ✅ | 16.15 local service, `recon` database created |
| DB roles | ✅ | `recon_rw` (loader) and `recon_ro` (read-only, used by tests + MCP) |
| PostgreSQL MCP server | ⏳ | installed, **disabled until a person enters the connection credentials** in MCP settings |
| Docker | ⚠️ | available but Docker Hub pulls are rate-limited (HTTP 429) on this host; local Postgres used instead |

---

## 5. Delivery Phases

### Phase 0 — Foundation and MCP enablement

MCP servers installed and reachable, Python/Robot toolchain pinned, repo scaffolding,
linting, pre-commit, CI skeleton, `.mcp.json` committed so every engineer's agent gets
the same servers.

### Phase 1 — POC / Pilot (CSV → PostgreSQL)

| ID | Item | MCP tooling used |
| --- | --- | --- |
| Pilot-01 | Header and metadata validation | rf-mcp `execute_step` + OperatingSystem/CSV keywords |
| Pilot-02 | Data-quality checks | rf-mcp step execution over contract-driven rules |
| Pilot-03 | PostgreSQL schema validation | PostgreSQL MCP `information_schema` + rf-mcp DatabaseLibrary |
| Pilot-08 | CSV → PostgreSQL loader (`recon_rw`) | rf-mcp `execute_step` over the loader keyword; idempotent per-run load |
| Pilot-04 | Key-based record comparison | rf-mcp + engine; PostgreSQL MCP for independent count/key checks |
| Pilot-05 | Transformation validation | rf-mcp live execution of expected-vs-actual derivations |
| Pilot-06 | Configuration and secrets separation | env YAML + MCP-managed credentials; zero secrets in git |
| Pilot-07 | CI execution and reports | rf-mcp `run_test_suite`; Robot reports published as CI artifacts |

### Phase 2 — Production hardening

| ID | Item |
| --- | --- |
| PROD-01 | Adapter architecture (stable source/target interfaces + registry) |
| PROD-02 | S3 source |
| PROD-03 | API source (rf-mcp RequestsLibrary keywords) |
| PROD-04 | Dataiku source |
| PROD-05 | Snowflake target |
| PROD-06 | MySQL target |
| PROD-07 | Security and secrets hardening (vault, least-privilege, secret scanning) |
| PROD-08 | Performance and scalability (chunked compare, hashing, parallel suites) |
| PROD-09 | Observability and audit (central run store, metrics, immutable audit trail) |
| PROD-10 | AI failure analysis and production rollout |

Phase 2 starts only after the Phase 1 exit gate below is met and stable.

---

## 6. First Implementation Success Criteria

The first implementation is successful when a **single command / test run** completes this
flow end to end with no manual comparison:

```
customer.csv  (100 valid data rows)
      │
      ├── pre-load validation   → header + metadata + data-quality rules pass
      │
      ├── load into PostgreSQL  (loader, recon_rw role)
      │
      ├── post-load validation  → schema matches contract
      │                           target count = 100
      │                           key-based comparison: 0 mismatches
      │                           transformation rules: 0 failures
      │
      └── result: PASS + Robot Framework report (report.html / log.html / run_summary.json)
```

Exit criteria:

1. `robot -d results tests/pilot` (or rf-mcp `run_test_suite`) returns rc = 0 on clean data.
2. A deliberately corrupted dataset (missing row, wrong type, bad transform) produces a
   FAIL with an accurate mismatch count and a readable diff sample.
3. No credentials in the repository; DB access via env config + MCP-managed credentials.
4. The same suite runs unchanged in CI and publishes reports as build artifacts.
5. Runbook lets a new engineer run it in under 15 minutes.

S3, API, Dataiku, Snowflake, and MySQL work does not begin until the above are met.

---

## 7. Production Definition of Done

- Multi-source support: CSV, API, S3, Dataiku
- Multi-target support: PostgreSQL, Snowflake, MySQL
- Reusable adapter design with documented interfaces
- Data-contract-driven validation (no hardcoded column logic in tests)
- Batch-level **and** record-level reconciliation
- Secure credential handling (no plaintext secrets anywhere)
- CI/CD execution on every relevant change and on schedule
- Centralized reporting with historical trend
- Auditability: every run traceable to contract version, environment, and commit
- Production-safe read-only access enforced at the role level
- Failure handling: clear failure-layer attribution and actionable diff samples

---

## 8. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Large datasets exhaust memory during compare | Chunked streaming + row hashing (PROD-08) |
| Credential sprawl across sources | MCP-managed credentials + single secret-store abstraction |
| Contract drift vs actual schema | Schema validation fails fast (Pilot-03) |
| Flaky comparisons (ordering, timezone, float precision) | Normalization layer with documented rules |
| AI-generated tests weakening assertions | Human PR review; assertion-change diff check in CI |
| Accidental production writes | `recon_ro` read-only role; MCP Postgres connection read-only |
| rf-mcp / MCP server unavailable in CI | CI runs plain `robot`; MCP is an authoring/debugging aid, not a runtime dependency |
| Docker Hub rate limits | Native Postgres install or an internal registry mirror |

---

## 9. Immediate Next Steps

1. Approve the PostgreSQL MCP credentials in MCP settings so the server enables.
2. Confirm the `customer` data contract fields and the target table DDL.
3. Work the checklist in `docs/TODO.md` stage by stage.
