# Implementation Plan: add-vcs-webhooks-context, Phase 3 (Durable ingestion)

## Overview

Implement tasks 3.1, 3.2, 3.3. Turn a normalized source event into durable, idempotent work: persist an inbox row first, then (asynchronously) fetch revision-bound diff through the phase 2 `VcsClient`, parse it with phase 1 `parse_diff`, and commit review job + structured context atomically. No HTTP webhook endpoints or provider authentication here (phase 4): this phase exposes services that phase 4 calls with an already-normalized event. OpenSpec `tasks.md` owns status.

Spec anchors: `specs/webhook-ingestion/spec.md` (Durable idempotent acceptance, Dispatch recovery, Persisted context), `specs/vcs-retrieval/spec.md`, design.md § Durable acceptance and recovery.

## Current state (verified)

- Models: `Repository` (unique `external_id`), `MergeRequest` (`external_id`, no uniqueness with repository), `ReviewJob` (status enum, no revision fields), `ContextPayload` (1:1 with job, `diff_text`, `token_count`). One Alembic revision `fb6eae330c65`.
- DB tests use in-memory SQLite (`tests/models/conftest.py`). There is NO PostgreSQL in tests or in the CI `checks` job (only `uv run pytest`). RQ tests use fakeredis.
- Queue: `app.queue.get_queue()`; worker is `python -m app.worker` (compose service `worker`), only a `ping` task exists.

## Architecture Decisions

- Inbox table `vcs_deliveries`: provider, delivery_id, normalized event fields (repository identity, PR/MR number, action, head_sha, base_sha if known, repo name/url, title/author/branches only if present in the event), status (`pending`, `processing`, `succeeded`, `failed`), attempts, `next_attempt_at`, lease (`locked_until`), `last_dispatched_at`, `last_error`, timestamps. Two uniqueness constraints: `(provider, delivery_id)` and `(provider, repository_external_id, pr_number, head_sha)`. The second one is the source-revision identity across deliveries.
- Provider scoping without altering the existing unique: `Repository.external_id = "<provider>:<provider repository id>"` (GitHub numeric repo id / full name decided by phase 4 normalization; GitLab numeric project id). Add a unique `(repository_id, external_id)` on `merge_requests` so concurrent upserts cannot duplicate an MR.
- Final guard against duplicate jobs: `review_jobs` gains `head_sha`, `base_sha` with unique `(merge_request_id, head_sha)`; `context_payloads` gains structured context JSON (JSONB on PostgreSQL, JSON elsewhere), provider metadata JSON, `schema_version`, and the revision identity. Existing rows stay valid (nullable/defaulted columns).
- Idempotency uses database constraints (`INSERT ... ON CONFLICT DO NOTHING` / catching `IntegrityError`) and `SELECT ... FOR UPDATE SKIP LOCKED` for claiming, never check-then-insert alone.
- Do not hold a DB transaction across HTTP: claim in one short transaction (status `processing`, lease), fetch + parse outside it, then commit job, context and delivery `succeeded` in one transaction guarded by the lease/status.
- Failure policy (bounded, default 5 attempts, exponential backoff via `next_attempt_at`):
  - retry: retryable `VcsError`s, `VcsRevisionChangedError`, `VcsDiffIncompleteError` (covers GitLab empty `changes_count` / async `diff_refs`);
  - terminal `failed`, no job/context created: auth, not found, diff too large, `DiffParseError`, attempts exhausted. `last_error` stores the typed error class and a redacted message.
- Dispatch is at-least-once and idempotent: enqueue with deterministic RQ job id `ingest-<delivery_id>`; the ingestion function exits harmlessly if the row is not `pending`/expired-lease. A recovery dispatcher re-enqueues rows that are `pending` and undispatched/stale, or `processing` with expired lease.
- Phase 3 depends only on ports: `VcsClient` is injected into the ingestion service; the worker entry builds real clients from settings. No network or DB at import time.
- Test infrastructure: PostgreSQL tests are selected by `TEST_DATABASE_URL` and skipped when unset locally; CI must provide a PostgreSQL service (and Redis for 3.3) so they run there.

## Task 3.1: Persistence migration and models

Description: Add the inbox model, new columns/constraints, and one Alembic revision; add the PostgreSQL test fixture.

Acceptance criteria:
- `alembic upgrade head` then `downgrade -1` then `upgrade head` succeed on PostgreSQL with pre-existing rows (backfilled/nullable columns); the ORM metadata matches the migration (no autogenerate drift).
- The database rejects a duplicate `(provider, delivery_id)`, duplicate revision identity, duplicate `(repository_id, external_id)` MR, and duplicate `(merge_request_id, head_sha)` job.
- Existing SQLite model tests still pass.

Verification: `rtk uv run pytest tests/models tests/db -q` (PostgreSQL tests with `TEST_DATABASE_URL`), Ruff, `mypy app tests alembic`, full suite.

Dependencies: Phase 2 not required at code level.

Files likely touched: app/models/vcs_delivery.py, app/models/{__init__,review_job,context_payload,merge_request}.py, alembic/versions/<rev>_ingestion.py, tests/db/conftest.py, tests/db/test_migration.py, tests/db/test_constraints.py.

Estimated scope: Large (about 9 files). Kept as one task because the migration is a single atomic schema step; the subagent must not widen it.

## Task 3.2: Transactional ingestion and duplicate handling

Description: Implement `accept_delivery` (idempotent inbox insert returning created / duplicate_delivery / duplicate_revision) and `ingest_delivery` (claim, fetch, parse, atomic commit, failure policy above) as plain services in `app/ingestion/`.

Acceptance criteria:
- Same delivery id twice, and two deliveries with the same revision, yield one inbox row and at most one review job/context, sequentially and under concurrent threads/sessions on PostgreSQL.
- Success creates repository (upsert), merge request (upsert), review job (`pending`) and context (raw diff, structured JSON, metadata, revision) in one transaction; any failure leaves no job or context.
- Error policy table implemented and tested with a fake `VcsClient`: retry scheduling, exhaustion to `failed`, terminal errors, parse error, lease handling (a second worker cannot process a claimed row; an expired lease is reclaimable).

Verification: `rtk uv run pytest tests/ingestion -q` with PostgreSQL, Ruff, mypy, full suite.

Dependencies: 3.1, phase 1 parser, phase 2 `VcsClient`.

Files likely touched: app/ingestion/{__init__,events,service}.py, tests/ingestion/test_accept.py, test_ingest.py, test_concurrency.py.

Estimated scope: Medium to Large (6 files).

## Task 3.3: RQ ingestion job and recovery dispatch

Description: Add the importable worker function `app.worker.ingestion.run_ingestion(delivery_id)`, an `enqueue_delivery` helper that never raises into the caller after durable acceptance, a recovery dispatcher (`dispatch_pending`) and a runnable loop (`python -m app.worker.recovery`) with compose wiring.

Acceptance criteria:
- Enqueue failure (Redis down) leaves the delivery `pending` and not lost; once Redis is back the dispatcher enqueues it and the worker completes ingestion.
- Crash windows covered: accepted-not-dispatched, dispatched-not-claimed, claimed-then-worker-died (lease expiry). Duplicate dispatch yields one job/context.
- Retries are bounded by the 3.2 policy, with `next_attempt_at` honored by the dispatcher.
- The worker module imports without DB/Redis/network; settings supply real `GitHubClient`/`GitLabClient`.

Verification: `rtk uv run pytest tests/worker tests/ingestion -q` (fakeredis for unit; real Redis + PostgreSQL integration marked and run in CI), Ruff, mypy, full suite.

Dependencies: 3.2.

Files likely touched: app/worker/ingestion.py, app/worker/recovery.py, app/ingestion/dispatch.py, docker-compose.yml, tests/worker/test_ingestion_job.py, tests/ingestion/test_dispatch.py.

Estimated scope: Medium.

## Checkpoint: Phase 3 complete

- [ ] `rtk uv run pytest -q` (with PostgreSQL and Redis), Ruff check/format, `mypy app tests alembic` clean.
- [ ] Migration upgrade/downgrade verified on PostgreSQL; concurrency test is deterministic (barrier-based, no sleeps for correctness).
- [ ] CI `checks` job provides PostgreSQL/Redis so integration tests are not silently skipped.
- [ ] `openspec validate add-vcs-webhooks-context --strict` passes; commit mode: after each task.
- [ ] Review with the user before planning phase 4.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| No PostgreSQL available locally or in CI; docker image pull may be slow | High | Subagent starts a throwaway `postgres` container (Docker is available); CI job gets a service container; tests skip only when `TEST_DATABASE_URL` is unset and CI asserts it is set. |
| Concurrency tests flaky | High | Barrier-synchronized threads with independent sessions; assert on final DB state, not timing. |
| SQLite-vs-PostgreSQL divergence (JSONB, ON CONFLICT, SKIP LOCKED) | Med | Use dialect-neutral types with JSONB variant; PostgreSQL-only paths are tested on PostgreSQL only. |
| Migration breaks existing rows | Med | Nullable/defaulted new columns; migration test seeds rows before upgrade. |
| Stuck `processing` rows after worker crash | Med | Lease expiry plus recovery dispatcher; covered by crash-window tests. |
| Large 3.1 and 3.2 scopes | Med | Subagent must stay within listed files; escalate rather than expanding. |

## Open Questions

- Editing `.github/workflows/ci-cd.yml` (PostgreSQL/Redis services) is required for the integration tests to run in CI. Default in this plan: include it in 3.1 (PostgreSQL) and 3.3 (Redis). Confirm.
- Recovery loop deployment: separate compose service `recovery` (planned) versus running it inside the worker process. Confirm or accept the default.
Resolved by the user: CI workflow edits (PostgreSQL in 3.1, Redis in 3.3) approved; recovery runs as a separate compose service `recovery`.

- Provider repository identity format for `Repository.external_id` (numeric id vs full name) is finalized in phase 4 normalization; 3.x treats it as an opaque string.
