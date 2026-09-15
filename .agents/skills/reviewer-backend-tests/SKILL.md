---
name: reviewer-backend-tests
description: Test review-service behavior and contracts, including business deduplication, context construction, candidate validation, history and summary publication. Use for the team's own backend tests.
---

Read [project context](../../rules/project-context.md), the affected [architecture section](../../rules/architecture.md), and [test plan](../../templates/test-plan.md). Choose cases that expose observable regressions in the affected component.

Use real PostgreSQL uniqueness for concurrent business-key tests and configured RabbitMQ services for redelivery/crash/DLQ tests. Different delivery IDs may represent the same automatic review; a new manual action at the same SHA must remain distinct. Replace external reader/Gateway/publisher ports only at unit-test boundaries.

For Context Builder test escalation/budget exhaustion, unresolved symbols and cache isolation. For rule/report changes run [contract checks](../../validation/README.md), including filtering and strict rejection. For Matcher test the successful same-PR/MR baseline, failed intervening runs and ambiguous matches; partial input cannot prove FIXED. For Publisher test update, deletion/recreation, concurrent retry and separate publication failure.

Resolve pytest, lint and integration commands from project configuration. Do not execute untrusted reviewed code in the runtime service or a privileged test environment. Report unit, contract, database, broker and model results separately with actual commands and remaining prerequisites.
