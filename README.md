# RobotFWDevinAIQA
QA Database Automation testing using Robot Framework using Devin AI Desktop IDE

## Data Reconciliation Testing Framework

This repo hosts an MCP-based, data-contract-driven reconciliation test framework
(Robot Framework + Python) that validates data moving from a source system into a
target database — no manual spreadsheet comparison.

- **Plan:** [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — architecture,
  MCP servers, delivery phases, success criteria.
- **Checklist:** [docs/TODO.md](docs/TODO.md) — stage-gated delivery checklist.

POC scope: CSV source → PostgreSQL 16 target, authored via the Robot Framework MCP
server (`rf-mcp`) with read-only verification through the PostgreSQL MCP server.
