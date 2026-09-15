# Backend development and review workflow

## Implement a task

Use [reviewer-backend](skills/reviewer-backend/SKILL.md) with [implementation input](templates/implement.md). For a direct model invocation, use [coding-system](prompts/coding-system.md) as the system message and the filled implementation template as the user message.

1. Read the issue, accepted API/event contract, affected callers, and project configuration. Identify the relevant FastAPI, application, SQLAlchemy, VcsReader/Publisher, Context Builder, LLM Gateway, Finding Validator/Matcher, and RabbitMQ boundaries.
2. State acceptance criteria and the transaction/authorization/idempotency behavior that must hold. Keep each function within the argument limit and group cohesive business inputs in typed objects.
3. Implement the task within its ownership boundaries. Add regression and contract tests using [reviewer-backend-tests](skills/reviewer-backend-tests/SKILL.md) and the [test plan](templates/test-plan.md).
4. Resolve the configured check targets, then run Ruff, Pylint, and pytest through uv. Use the project's Docker services for PostgreSQL/RabbitMQ integration tests. Apply the configured Skylos check where appropriate. Report commands, exit status, and unavailable checks honestly.
5. Review the diff and prepare the [PR description](templates/pull-request.md). Commit/push/open a PR only when the current task authorizes delivery. Human approval follows [project context](rules/project-context.md).

Do not substitute a mock for a database uniqueness test, a queue durability test, or a resource authorization check. If a required service or shared contract is unavailable, finish independent work and identify the exact dependency and its owner.

## Review a change

Use [reviewer-diff-review](skills/reviewer-diff-review/SKILL.md). Obtain immutable base/head snapshots and trusted source locations, changed-line relations and immutable RuleSet, supply the [review input](prompts/review-user.md), then validate the returned report using [validation](validation/README.md). The process prioritizes confirmed security and behavioral defects, records context gaps, and separates report generation from comment publication.

## Run contract checks locally

The [validation README](validation/README.md) contains executable commands and expected exit behavior. Its fixtures are deterministic regression cases for the report protocol. Model-quality evaluation additionally needs the configured model through LLM Gateway, version, parameters, and a labeled set of diffs; record those results separately.

## Design-driven task routing

Use the relevant [architecture section](rules/architecture.md) for component invariants and [review contract](rules/review-contract.md) for output changes. Candidate schema 2.0.0, trusted context assembly, prompts and validator callers must migrate together in both repositories. The strict CLI rejects invalid reports; filtering is for valid envelopes with independently checked candidates and safe diagnostics. Persist only validated findings, then match history and update the summary through their owned contracts.
