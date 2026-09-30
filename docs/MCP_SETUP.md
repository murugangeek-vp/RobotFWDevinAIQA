# MCP Setup and Usage Rules

Two MCP servers are part of this project's toolchain. `.mcp.json` at the repo
root registers both for every engineer's agent.

## Servers

| Server | Role | Runtime dependency? |
| --- | --- | --- |
| `robotframework` (rf-mcp / `robotmcp`, STDIO) | Authoring aid: keyword discovery, live `execute_step`, `build_test_suite`, `run_test_suite` | **No** — CI runs plain `robot` |
| `postgres` (read-only) | Second-opinion `information_schema` / count queries while triaging mismatches | **No** |

## Install

```bash
uv tool install "rf-mcp[database,api]"   # provides robotmcp
robotmcp doctor                           # should report healthy
```

PostgreSQL MCP runs the official `mcp/postgres` image (`.mcp.json`); it reads
its DSN from the `RECON_RO_DSN` environment variable — set it locally or via
MCP settings. **Never commit a DSN** — `.mcp.json` must stay credential-free
(checked by TODO item M-06).

## Usage rules (binding)

1. **Postgres MCP is read-only.** It connects as `recon_ro`. Never use it for
   INSERT/UPDATE/DELETE/DDL — the loader path (`Load Source Into Target`,
   `Execute Write Sql` for test seeding) uses `recon_rw` by design.
2. **`find_keywords` / `get_keyword_info` before authoring.** Reuse approved
   keywords (`resources/keywords/*.resource`, `ReconciliationLibrary`); do not
   invent a keyword that already exists.
3. **No unverified `.robot` files.** A suite is committed only after its steps
   passed live via `execute_step` / `execute_batch` and the suite was emitted by
   `build_test_suite`, or verified end-to-end with `run_test_suite` / `robot`.
4. **Never change expected results to force a green run.** Assertion changes go
   through PR review (and the CI assertion-diff check once PROD-10 lands).
