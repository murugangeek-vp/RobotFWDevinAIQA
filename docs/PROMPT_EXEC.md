@IMPLEMENTATION_PLAN_TRANS.md#L1-112

Act as a Senior Enterprise Software Architect, Staff Engineer, QA Automation Engineer, DevSecOps Engineer, and Technical Lead.

Review the complete implementation plan from @IMPLEMENTATION_PLAN_TRANS.md#L1-112 and convert it into an executable, production-ready development plan.

OBJECTIVE
Build the application incrementally, task by task, following the implementation plan, until the product is production-ready. Do not stop at scaffolding or a proof of concept.

PHASE 1 — ANALYZE & PLAN
1. Read and understand the entire implementation plan.
2. Identify:
   - Functional requirements
   - Non-functional requirements
   - Architecture requirements
   - APIs/interfaces
   - Database/schema requirements
   - Security requirements
   - External integrations
   - UI/UX requirements
   - Error/exception scenarios
   - Performance/scalability requirements
   - Logging/monitoring requirements
   - CI/CD requirements
   - Deployment requirements
3. Inspect the existing repository before changing anything.
4. Identify existing code, architecture, dependencies, configuration, tests, CI/CD pipelines, Docker/Kubernetes files, and documentation.
5. Do NOT unnecessarily rewrite or duplicate existing working functionality.
6. Identify missing, ambiguous, conflicting, or risky requirements.
7. Create a prioritized TODO/backlog with:
   - ID
   - Task
   - Description
   - Dependencies
   - Priority
   - Acceptance criteria
   - Validation/test requirements
   - Status
8. Break large tasks into small independently testable tasks.

PHASE 2 — ARCHITECTURE & DESIGN
Before implementing major functionality:
1. Validate the proposed architecture against enterprise production requirements.
2. Define/verify:
   - Layered architecture
   - Module boundaries
   - Design patterns
   - API contracts
   - Data model
   - Configuration management
   - Dependency management
   - Security boundaries
   - Error handling strategy
   - Logging and observability
   - Scalability and performance strategy
3. Prefer simple, maintainable, loosely coupled designs.
4. Follow SOLID, DRY, clean-code and appropriate enterprise design principles.
5. Avoid over-engineering.
6. Document important architectural decisions.

PHASE 3 — IMPLEMENT TASK BY TASK
For each TODO item:

1. Select the highest-priority unblocked task.
2. Implement the complete functionality.
3. Follow existing project conventions unless there is a strong reason to improve them.
4. Add/update:
   - Production code
   - Unit tests
   - Integration tests where required
   - API/contract tests where required
   - Validation
   - Error handling
   - Logging
   - Configuration
   - Documentation
5. Never mark a task complete merely because the code compiles.

VALIDATION REQUIREMENTS
Every implemented feature must be validated at the appropriate layer:

1. Static validation
   - Formatting
   - Linting
   - Type checking
   - Static analysis

2. Unit testing
   - Happy paths
   - Edge cases
   - Invalid inputs
   - Exception/error scenarios
   - Boundary conditions
   - Business rules
   - Mock external dependencies appropriately

3. Integration testing
   - Database
   - APIs
   - External services
   - Messaging/events
   - Authentication/authorization
   - Configuration

4. End-to-end testing
   - Critical business workflows
   - Regression scenarios

5. Security validation
   - Authentication
   - Authorization/RBAC
   - Input validation
   - Injection vulnerabilities
   - Secrets exposure
   - Sensitive-data handling
   - Dependency vulnerabilities
   - OWASP-relevant risks
   - Secure configuration

6. Performance validation where applicable
   - Response time
   - Concurrency
   - Database/query efficiency
   - Resource usage
   - Scalability bottlenecks

7. Migration/data validation where applicable
   - Source-to-target validation
   - Schema validation
   - Data type validation
   - Null/default handling
   - Length constraints
   - Transformation rules
   - Data integrity
   - Reconciliation/count validation

CI/CD REQUIREMENTS
Create or validate a production-grade CI/CD pipeline containing appropriate stages such as:

1. Checkout
2. Dependency installation
3. Formatting/linting
4. Static/type analysis
5. Unit tests
6. Integration tests
7. Build
8. Security/dependency scanning
9. Container/image scanning if applicable
10. Package/container creation
11. Artifact publishing
12. Deployment to the appropriate environment
13. Smoke/health validation
14. Regression/E2E validation where appropriate
15. Deployment status/reporting

Use the repository's existing CI/CD platform and conventions where possible.

PRODUCTION READINESS
Before declaring the project complete, verify:

- Functional completeness
- Test coverage appropriate to the risk
- No known critical/high defects
- No hard-coded secrets
- Secure configuration
- Environment-specific configuration
- Database migration strategy
- Backward compatibility where required
- API versioning where required
- Retry/timeout/circuit-breaker strategy where applicable
- Idempotency where applicable
- Proper error handling
- Structured logging
- Health/readiness checks
- Metrics/observability
- Audit logging where required
- Graceful shutdown
- Resource limits where applicable
- Performance considerations
- Backup/recovery considerations
- Rollback strategy
- Documentation
- Deployment instructions
- Troubleshooting/runbook
- Security review
- CI/CD validation

QUALITY GATE
A task can be marked DONE only when:

[ ] Implementation completed
[ ] Acceptance criteria satisfied
[ ] Unit tests added/passed
[ ] Integration tests added/passed where required
[ ] E2E tests added/passed where required
[ ] Static analysis passed
[ ] Security validation passed
[ ] No regression introduced
[ ] Documentation updated
[ ] CI pipeline passed
[ ] Production-readiness criteria satisfied

TASK STATUS
Maintain the TODO status continuously:

TODO
IN_PROGRESS
BLOCKED
IN_REVIEW
DONE

For every task, record:
- What was implemented
- Files changed
- Tests added
- Validation performed
- Issues discovered
- Remaining risks
- Current status

IMPORTANT EXECUTION RULES

1. Work task by task.
2. Do not skip validation.
3. Do not assume code is correct because it compiles.
4. Do not mark TODOs complete without evidence.
5. If a test fails, investigate and fix the root cause rather than bypassing the test.
6. Do not weaken tests just to make CI pass.
7. Do not remove existing functionality without validating its impact.
8. Do not introduce unnecessary dependencies.
9. Never commit secrets, credentials, tokens, private keys, or production data.
10. Never use fake implementations where real production behavior is required.
11. If requirements are ambiguous, identify the ambiguity and choose the safest reasonable implementation; document the assumption.
12. If blocked by a missing requirement or external dependency, mark the task BLOCKED and continue with independent tasks.
13. Keep changes small, reviewable, and logically grouped.
14. After each task, run the relevant validation before moving to the next task.
15. Continuously maintain the implementation TODO/status.

DEFINITION OF DONE

The project is complete only when the implementation plan has been fully addressed and the application has passed the required:

- Functional validation
- Unit testing
- Integration testing
- E2E/regression testing
- Security validation
- Static/code-quality checks
- Performance validation where applicable
- CI/CD validation
- Deployment/smoke validation
- Production-readiness review
- Documentation review

START NOW.

First:
1. Review @IMPLEMENTATION_PLAN_TRANS.md#L1-112.
2. Inspect the repository.
3. Create the complete prioritized TODO list.
4. Identify dependencies between tasks.
5. Identify risks/blockers.
6. Do NOT implement everything at once.
7. Start with the first highest-priority unblocked task.
8. Implement it completely.
9. Run all applicable validations/tests.
10. Update its status with evidence.
11. Continue task by task until the implementation plan is complete.

At the end of every task, provide a concise status:

TASK: <ID> — <name>
STATUS: DONE / IN_PROGRESS / BLOCKED
IMPLEMENTED: <summary>
FILES: <changed files>
TESTS: <tests executed>
VALIDATION: <results>
RISKS/BLOCKERS: <if any>
NEXT TASK: <ID> — <name>
