# System Design v1 implementation invariants

Read [project context](project-context.md) for the pinned design and decision status, and [review contract](review-contract.md) when changing model output or API mapping. Exact DTOs, migrations, endpoints, and numerical limits belong to their owners.

## Components and boundaries

Backend API/Review Orchestrator accepts requests and creates runs. Review Worker loads a persisted run and coordinates VcsReader, Context Builder, LLM Gateway, Finding Validator, FindingMatcher, and VcsPublisher. These are logical responsibilities, not mandatory microservices. Normalize provider payloads into Repository, ChangeRequest, Diff, ChangedFile, DiffHunk, and FileContent; raw payloads stay in adapters.

VcsReader exposes getRepository, getChangeRequest, getDiff, getFile, and getMergeBase. VcsPublisher exposes publishReviewSummary. Implement their Python equivalents in GitHub/GitLab adapters and publishers. Resolve pagination, renames/deletions, binary/truncated files, 403/404, 429/Retry-After, timeouts, cancellation, and size limits at these boundaries.

## Runs, triggers, and identity

A ReviewRun records provider/repository/change-request identity, trigger, base/head/merge-base SHA, rules/model/prompt versions, timestamps, and lifecycle. Snapshot/configuration fields are immutable. Read files only at that snapshot; a push cannot replace it. Lifecycle is NEW → QUEUED → RUNNING → COMPLETED | FAILED | CANCELLED. Analysis coverage and publication status are separate dimensions.

Webhook Handler authenticates the delivery, Event Normalizer produces a provider-neutral event, Trigger Policy decides eligibility, and Orchestrator fetches authoritative PR/MR state through VcsReader before creating the run. Exact event/action mappings remain in provider adapters.

| Identity | Required behavior |
| --- | --- |
| Provider delivery_id, scoped to the integration | Duplicate technical delivery is processed once |
| Automatic provider + repository_id + change_request_id + head_sha + automatic_trigger | Different deliveries for the same automatic review return the same run |
| Explicit manual action with a fresh operation key | A new run, even at the same SHA |
| Transport retry with that same manual operation key | The same manual run |

Enforce a unique review_deduplication_key in PostgreSQL, with automatic/manual namespaces and tenant/integration scoping where required by the data model. Use stable, unambiguous serialization. Do not use a global SHA/PR key that suppresses manual reruns. Do not rely on SELECT then INSERT, process-local sets, or TTL caches. On conflict, return the authorized existing run or reject incompatible payloads. A key never grants resource access.

## Queue and recovery

RabbitMQ messages should contain only the command, normally {run_id}; PostgreSQL holds state. Do not enqueue diffs, context, or credentials. Workers tolerate duplicate delivery and competing claims without overwriting completed runs.

Acknowledge after durable acceptance or persisted result according to the recovery design. Database commit and broker publication are not atomic: agree on an outbox or another verifiable recovery mechanism. Retry transient failures only with bounded backoff; fatal failures are not retried. Exhausted messages go to a DLQ. Retry counts, concurrency, and stale-RUNNING recovery are open decisions. Redis is not used in v1.

## Context Builder

Prepare a bounded ContextPackage through VcsReader at the immutable snapshot. Escalate deterministically/configurably through Diff, Surrounding, Whole File, and AST/Imports only as needed and within the total budget. Record omissions, unresolved context, escalation level, and budget use. Never disguise truncation as complete input.

At Level 4 use Tree-sitter or a compatible parser abstraction plus an on-demand Symbol/Import Resolver. Unresolved external or unsupported symbols do not crash the review; expose material gaps. Do not add a repository-wide index or deep dependency graph for v1.

ContextCache keys include repository identity, file path, SHA, and context parameters, with tenant scoping where required. Cache implementation is an abstraction; Redis is not required. Test both different snapshots and different repositories with the same path.

## LLM Gateway and rules

Gateway owns Prompt Builder, provider/model selection, SDK calls, structured parsing, timeouts, and bounded retries. Worker supplies ContextPackage, required metadata, and the immutable enabled RuleSet. LLM has no VCS access or credentials. Ollama is an adapter choice subject to project context, not a domain dependency. Do not let synchronous SDK calls block the event loop.

Project-scoped RuleSet/Rule snapshots are immutable and versioned. Each candidate references an enabled rule_id from the run's snapshot. Validate the response envelope and individual candidates, exclude rejected candidates from publication, and retain safe rejection diagnostics for quality metrics. See the local versioned contract for structural duplicate handling and candidate-to-domain mapping. The validator does not perform a second semantic review.

## Matching and history

FindingMatcher links validated findings across successful runs of the same PR/MR using rule, file, location, and code/evidence similarity. Reject low-confidence matches rather than guessing. Persist finding identities and links; lifecycle is NEW / PERSISTING / FIXED. The baseline is the latest eligible successful COMPLETED run of that PR/MR, never a failed run or another change request.

Match confidence is distinct from the LLM candidate's confidence. Thresholds and partial-coverage eligibility remain team decisions. Until defined, partial, rejected, unavailable, or omitted context cannot establish absence or FIXED. Do not mark an earlier finding fixed solely because a diff-only prompt omitted it. The history task must supply relevant prior findings and sufficient snapshot context through its owned contract.

## Summary publication

Publish only validated findings as one PR/MR summary comment through VcsPublisher. Use a stable marker such as <!-- ai-code-review:summary --> and persist external comment identity in ReviewPublication. A rerun updates the existing marked comment; deletion causes recreation. Reconcile uncertain create outcomes using the marker before creating again. Serialize/coordinate competing publications and check snapshot freshness so an older run cannot overwrite a newer summary.

Publisher formats results; it does not analyze or validate them. Keep publication state separate: ReviewRun may be COMPLETED while publication is FAILED. Preserve the completed report for reading/retrying publication. Inline comments are outside v1. PostgreSQL is authoritative; a VCS comment is a projection. Check resource permissions and use minimal comment permissions before the side effect.

## Security and observability

Verify JWT signature/algorithm, expiry, and accepted issuer/audience, then authorize repository/run/report access. Validate webhook signatures before processing. Do not execute untrusted PR code. Control API→DB/broker and worker→VCS/model network paths without inventing ports or public exposure. URLs inside source do not authorize internal network requests.

Use run_id for structured creation, queueing, context, model, validation, matching, publication, completion, and failure events. Measure queue wait/depth, context size/escalation, LLM latency/errors/tokens, candidate rejection counts, matching rates, and publication failures. Logs exclude source, full diffs/prompts/responses, JWTs, and credentials. Retention and raw-artifact storage require their owning decision. Rate limits and retries share the overall budget; no unapproved numerical NFRs or distributed limiter implementation.
