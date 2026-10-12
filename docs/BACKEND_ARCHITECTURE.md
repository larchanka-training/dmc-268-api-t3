# Backend Architecture

## Layering (Clean/Hexagonal)

```
Router (FastAPI) -> Service (business logic) -> Repository (data access) -> LLM Gateway (external LLM calls)
                                                        |
                                                   PostgreSQL
```

- **Router** — FastAPI path operation functions. Parse/validate HTTP input, call a Service, return a response model. No business logic, no direct DB or LLM access.
- **Service** — orchestrates a use case (e.g. "review a merge request"). Calls one or more Repositories and the LLM Gateway. No FastAPI or SQLAlchemy imports.
- **Repository** — one class per aggregate (`RepositoryRepo`, `MergeRequestRepo`, `ReviewJobRepo`, `FindingRepo`), wrapping SQLAlchemy `Session` queries against the models in `app/models/`. No business logic.
- **LLM Gateway** — the `app/llm/` package (separate PR): abstracts over LLM providers, returns validated `ReviewResult` Pydantic objects. Services depend on the Gateway's interface, never on a concrete provider.

The target shape above is implemented for one use case so far: VCS webhook ingestion (next section). The review pipeline itself (LLM review of a stored context, validation, publication) is not implemented. The code uses `MergeRequest`/`ReviewJob` (`pending`/`running`/`completed`/`failed`), not the `ReviewRun` (`NEW` -> `QUEUED` -> ...) of [`SYSTEM_DESIGN.md` §3](SYSTEM_DESIGN.md#3-reviewrun-and-immutable-snapshot); this divergence remains and is not migrated by the webhook change.

## Webhook ingestion

Setup, configuration and responses are in [`VCS_SETUP.md`](VCS_SETUP.md); this section is the code structure.

```
POST /webhooks/{github,gitlab}
  -> app/api/webhooks.py        read raw body (25 MiB cap), map errors to 400/401/413/503
  -> app/webhooks/              authenticate raw bytes (auth.py), validate and normalize to VcsEvent
  -> app/ingestion/dispatch.py  accept_and_dispatch: commit the inbox row, then enqueue
  -> app/ingestion/service.py   accept_delivery: idempotent insert into vcs_deliveries
  -> Redis / RQ                 job app.worker.ingestion.run_ingestion(delivery_id)

worker: app/worker/ingestion.py -> app/ingestion/service.py ingest_delivery
  -> app/vcs/                   GitHubClient / GitLabClient fetch metadata and the complete raw diff
  -> app/context/               parse_diff builds the structured context
  -> PostgreSQL                 repositories, merge_requests, review_jobs, context_payloads, vcs_deliveries

recovery: app/worker/recovery.py -> dispatch_pending re-enqueues stuck deliveries
```

- **Router** (`app/api/webhooks.py`) holds no business logic. It reads the raw body, runs authentication and normalization in a threadpool, and calls `accept_and_dispatch`. Authentication failure is `401` (the same answer for a wrong and for an unconfigured secret), a payload that fails validation is `400`, an unsupported event or action is `200 ignored`, and a database outage is `503`.
- **Authentication and normalization** (`app/webhooks/`) are pure functions over headers and bytes: HMAC verification for GitHub, Standard Webhooks signature or token comparison for GitLab, then a provider normalizer producing a frozen `VcsEvent` (`app/ingestion/events.py`) or an `IgnoredEvent`. They do not touch the database.
- **Ingestion service** (`app/ingestion/`) takes a `Session` factory and a `VcsClient` and contains the orchestration. It imports SQLAlchemy directly and issues its statements itself; there is no separate repository class for these tables. This differs from the Router -> Service -> Repository target above and is intentional for this change: the idempotency rules depend on PostgreSQL features (`ON CONFLICT`, `FOR UPDATE SKIP LOCKED`) that are kept next to the code that relies on them.
- **VCS adapters** (`app/vcs/`) implement the `VcsClient` protocol (`fetch_revision`) for GitHub and GitLab. They only read, have a timeout, classify errors as retryable or terminal, fail instead of truncating an oversized diff, and refuse a revision whose head SHA changed during the fetch. Credentials are read from settings by the worker job (`SettingsVcsClient`); they are never part of a queue message or a stored row.
- **Context parser** (`app/context/`) turns the raw unified diff into the `DiffContext` schema stored in `context_payloads.structured_context`. Source code is treated as untrusted data and is never executed.
- **RQ job** (`app/worker/ingestion.py`) carries only the delivery id. The job id is deterministic (`ingest-<delivery id>`).

### Acceptance and deduplication

`accept_delivery` inserts the normalized event into `vcs_deliveries` with `ON CONFLICT DO NOTHING`. Two unique constraints decide a duplicate: `(provider, delivery_id)` (the same technical delivery redelivered) and `(provider, repository_external_id, pr_number, head_sha)` (the same revision announced by a different delivery). The row is committed before the queue is touched, and only a newly created row is enqueued. If Redis is down the row stays durable and the recovery process dispatches it.

### Ingestion job

1. **Claim** (short transaction): lock the row with `FOR UPDATE SKIP LOCKED` if it is `pending` and due, or `processing` with an expired lease; increment `attempts` and set `locked_until` (the lease). A row that is not claimable is a no-op, so duplicate or late jobs are harmless. A row that reached the attempt limit becomes `failed`.
2. **Fetch** outside any transaction through the `VcsClient`, and verify that the retrieved head equals the delivery head.
3. **Commit** in one transaction, guarded by the lease: upsert `Repository` (key `<provider>:<id>`), upsert `MergeRequest`, insert `ReviewJob` for `(merge_request_id, head_sha)` with `ON CONFLICT DO NOTHING`, insert the `ContextPayload` for a new job, and mark the delivery `succeeded`. A worker that lost its lease finishes with no effect.
4. **Failure**: retryable errors (transient VCS errors, a changed head, an incomplete diff) return the row to `pending` with exponential backoff; terminal errors and exhausted attempts mark it `failed`. `last_error` is truncated and redacted. Other exceptions propagate and the row is recovered when its lease expires.

The attempt limit, backoff, lease length, diff size limit and timeouts are provisional values; the open decisions stay in [`SYSTEM_DESIGN.md` §15](SYSTEM_DESIGN.md#15-open-decisions) and the current values are listed in [`VCS_SETUP.md` §5](VCS_SETUP.md#5-configuration).

## Data Model

See `docs/architecture/erd.md` for the ER diagram. Entities, in `app/models/`:

| Entity | Table | Purpose |
|---|---|---|
| `Repository` | `repositories` | A tracked Git repository (e.g. a GitLab/GitHub project). |
| `MergeRequest` | `merge_requests` | One MR/PR opened against a `Repository`. |
| `ReviewJob` | `review_jobs` | One review run for a `MergeRequest`, with a `status` state machine (`pending` -> `running` -> `completed`/`failed`). Carries `head_sha`/`base_sha` (nullable); unique on `(merge_request_id, head_sha)`. Webhook ingestion creates one job per revision; nothing advances its status yet. |
| `ContextPayload` | `context_payloads` | The diff + metadata assembled for exactly one `ReviewJob` (1:1). Besides `diff_text`, `language` and `token_count` it stores `structured_context` (parsed diff, JSONB), `provider_metadata` (JSONB, pull request metadata and the originating delivery), `schema_version`, and the `head_sha`/`base_sha` it was built from. |
| `Finding` | `findings` | One review comment/finding produced for a `ReviewJob` (1:many). |
| `VcsDelivery` | `vcs_deliveries` | Durable inbox of accepted, normalized webhook deliveries with a processing state (`pending` -> `processing` -> `succeeded`/`failed`), `attempts`, `next_attempt_at`, the lease `locked_until`, `last_dispatched_at` and `last_error`. It has no foreign keys: the provider repository identity is stored as text. Unique on `(provider, delivery_id)` and on `(provider, repository_external_id, pr_number, head_sha)`. |

`Repository.external_id` is unique and holds `<provider>:<provider repository id>`; `MergeRequest` is unique on `(repository_id, external_id)`, where `external_id` is the pull request number or merge request iid. All of this comes from migration `a3c1e5d97b42`.

## Migrations

Alembic (`alembic/`), configured to read the DB URL from `app.config.settings.database_url`. Run `uv run alembic upgrade head` to apply.

## Queue and worker

Background work goes through Redis with [RQ](https://python-rq.org/):

- `app/queue.py` — `get_redis()` / `get_queue()`. Code enqueues with `get_queue().enqueue(func, *args)`; the job function must live in an importable module (not `__main__`).
- `app/worker/` — the worker process, `python -m app.worker`, run from the same image as the API with a different command. `ingestion.py` holds the `run_ingestion` job (see [Webhook ingestion](#webhook-ingestion)); `tasks.py` holds the `ping` smoke job. The review job itself (issue #13) is not implemented.
- `app/worker/recovery.py` — the `recovery` process, `python -m app.worker.recovery`, same image. It is a loop that calls `dispatch_pending` every `RECOVERY_INTERVAL_SECONDS` and re-enqueues deliveries that were accepted but never dispatched, were dispatched but not claimed within `RECOVERY_STALE_AFTER_SECONDS`, are due for retry, or whose lease expired. Tick errors are logged and do not stop the loop; it exits on SIGINT/SIGTERM. Without it a delivery whose enqueue failed stays `pending`. It runs as the `recovery` compose service and the `dmc268-recovery` production container ([`DEVOPS.md`](DEVOPS.md)).
- `python -m app.worker.smoke` enqueues `ping` and waits for its result — CI and the deploy pipeline use it to prove API → Redis → worker works.
