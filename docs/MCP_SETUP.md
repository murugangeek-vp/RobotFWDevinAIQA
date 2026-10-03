# MCP Setup and Usage Rules

Two MCP servers are registered for every engineer through `.mcp.json`. An
optional Snowflake-managed MCP server can also be registered locally for the
trial environment; that local registration is not committed.

## Servers

| Server | Role | Runtime dependency? |
| --- | --- | --- |
| `robotframework` (rf-mcp / `robotmcp`, STDIO) | Authoring aid: keyword discovery, live `execute_step`, `build_test_suite`, `run_test_suite` | **No** — CI runs plain `robot` |
| `postgres` (read-only) | Second-opinion `information_schema` / count queries while triaging mismatches | **No** |
| `snowflake` (managed HTTP, optional trial) | Independent bounded SELECT-only checks against `RECON.PUBLIC.CUSTOMER` | **No** |

## Install

```bash
uv tool install "rf-mcp[database,api]"   # provides robotmcp
robotmcp doctor                           # should report healthy
```

PostgreSQL MCP runs the official `mcp/postgres` image (`.mcp.json`); it reads
its DSN from the `RECON_RO_DSN` environment variable — set it locally or via
MCP settings. **Never commit a DSN** — `.mcp.json` must stay credential-free
(checked by TODO item M-06).

## Snowflake trial MCP

The Snowflake MCP server is Snowflake-managed and uses OAuth. Snowflake does
not support dynamic client registration for these endpoints, so a trial-local
OAuth security integration is provisioned by SQL instead.

1. Generate the SQL after the trial fixture exists:

   ```powershell
   .\.venv\Scripts\python.exe scripts\prepare_snowflake_trial.py --mcp-sql
   ```

2. Review `results_sf\mcp_setup.sql`, then run all of it in a Snowsight
   worksheet for the intended trial account. The script verifies the account
   identifier and fixture count, creates `RECON.PUBLIC.RECON_MCP_RO`, grants it
   only to `RECON_RO_ROLE`, creates the `RECON_MCP_OAUTH` integration, and sets
   your Snowflake user's default role/warehouse to the least-privileged trial
   values.

3. The final `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS` result contains a client ID and
   client secret. Keep the secret local; do not paste it into chat or commit it.
   Store both values only in the Git-ignored local registration:

   ```powershell
   $clientId = Read-Host "Snowflake MCP client ID"
   $secure = Read-Host "Snowflake MCP client secret" -AsSecureString
   $secret = [System.Net.NetworkCredential]::new("", $secure).Password
   $configPath = ".\.devin\mcp_config.local.json"
   $config = Get-Content $configPath -Raw | ConvertFrom-Json
   $config.mcpServers.snowflake.oauthClientId = $clientId.Trim('"')
   $config.mcpServers.snowflake.oauthClientSecret = $secret.Trim('"')
   $config | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
   ```

4. Sign in with the locally registered server. If `devin` is not on `PATH`, use
   the bundled executable:

   ```powershell
   devin mcp login snowflake
   # or:
   & "$env:LOCALAPPDATA\Programs\Devin\resources\app\extensions\windsurf\devin\bin\devin.exe" mcp login snowflake
   ```

The OAuth callback is `http://localhost:8765/callback`; non-TLS is allowed only
for that loopback URI. The SQL tool is configured `read_only: true`, with a
60-second query timeout and `RECON_WH`.

Trial limitation observed: MCP `initialize` and `tools/list` succeed, and the
server advertises `read_only_sql`, but `tools/call` is rejected by Snowflake
with `Access denied for trial accounts` (399504). Independent SQL evidence via
managed MCP therefore requires a paid or organizational account; do not treat
the trial MCP setup as query evidence. The current Devin/Windsurf gateway also
returned HTTP 400 during client-side streamable-HTTP/SSE discovery even though
manual MCP JSON-RPC `initialize` and `tools/list` succeeded.

Use MCP only for independent SELECT/metadata checks. The Python connector and
Robot reports remain the authoritative automated evidence.

## Usage rules (binding)

1. **Postgres MCP is read-only.** It connects as `recon_ro`. Never use it for
   INSERT/UPDATE/DELETE/DDL — the loader path (`Load Source Into Target`,
   `Execute Write Sql` for test seeding) uses `recon_rw` by design.
2. **Snowflake MCP is read-only and trial-scoped.** Use `RECON_RO_ROLE`,
   `RECON_WH`, and the generated `RECON_MCP_RO` endpoint only for SELECT and
   metadata checks. Do not use it to load, alter, truncate, or drop objects, and
   do not apply the trial registration to an organizational account.
3. **`find_keywords` / `get_keyword_info` before authoring.** Reuse approved
   keywords (`resources/keywords/*.resource`, `ReconciliationLibrary`); do not
   invent a keyword that already exists.
4. **No unverified `.robot` files.** A suite is committed only after its steps
   passed live via `execute_step` / `execute_batch` and the suite was emitted by
   `build_test_suite`, or verified end-to-end with `run_test_suite` / `robot`.
5. **Never change expected results to force a green run.** Assertion changes go
   through PR review (and the CI assertion-diff check once PROD-10 lands).
