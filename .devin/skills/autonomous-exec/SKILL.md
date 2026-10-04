---
name: autonomous-exec
description: Execute a TODO/implementation plan autonomously — implement, validate, and update status without pausing between tasks
allowed-tools:
  - read
  - edit
  - grep
  - glob
  - exec
---

AUTONOMOUS EXECUTION MODE

You are authorized to continue through the TODO list without waiting for confirmation after every successful task.

However:
- Stop and ask for clarification only when a decision materially affects architecture, security, data integrity, public API behavior, cost, or irreversible production changes.
- For normal implementation decisions, follow established project conventions and documented assumptions.
- Never perform destructive production/database operations without explicit approval.
- Before modifying shared/public interfaces, verify compatibility and update tests/documentation.
- Keep the repository in a buildable/testable state after each completed task.

Do not merely tell me what should be done.
Actually implement the work, validate it, update the TODO status, and proceed to the next task.
