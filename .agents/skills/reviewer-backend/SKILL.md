---
name: reviewer-backend
description: Implement Python/FastAPI review-service tasks using the project's run, VCS, context, model, finding and publication boundaries. Use for backend code changes.
---

Read [development rules](../../rules/development.md), [project context](../../rules/project-context.md), and the relevant [architecture sections](../../rules/architecture.md). Select exact paths, versions and commands from the working branch and the task's owned contracts.

Identify the affected component: API/Orchestrator, Worker, VcsReader/Publisher, Context Builder, LLM Gateway, Finding Validator, Matcher, or persistence. Preserve logical boundaries without creating unrequested services. Keep provider/model specifics in adapters and avoid network work during imports.

For run creation distinguish webhook delivery IDs, the automatic business key, and manual operation keys. Check snapshot/RuleSet immutability, PostgreSQL uniqueness, queue crash windows, transient retry budgets and DLQ routing. For context tasks enforce bounded escalation and repository/path/SHA/parameter cache isolation. For matching/publication preserve the eligible successful baseline and one maintained PR/MR summary.

When changing model output or DTO mapping, read [review contract](../../rules/review-contract.md). Keep candidate validation, database IDs/lifecycle, and API serialization separate. Incomplete evidence must not establish FIXED. Keep completed analysis readable if publication fails.

Use the [implementation template](../../templates/implement.md) and relevant [test cases](../../templates/test-plan.md). Report changed behavior, affected contracts, actual checks and unresolved owning decisions. Mock checks do not prove PostgreSQL/RabbitMQ recovery or model quality.
