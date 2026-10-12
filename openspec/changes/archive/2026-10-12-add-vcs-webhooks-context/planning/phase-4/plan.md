# Implementation Plan: add-vcs-webhooks-context, Phase 4 (Webhook transport)

## Overview

Implement tasks 4.1 and 4.2 only. 4.1 adds framework-free provider authentication over raw request bytes and provider payload validation/normalization into the existing `VcsEvent`. 4.2 adds `POST /webhooks/github` and `POST /webhooks/gitlab`, which authenticate, normalize, and hand the event to the phase 3 durable acceptance (`accept_delivery` + `enqueue_delivery`). No replay script, CI smoke job, setup documentation, or deploy wiring (phase 5). OpenSpec `tasks.md` owns status.

Spec anchors: `specs/webhook-ingestion/spec.md` (Authenticated delivery, Supported source events, Durable idempotent acceptance, Persisted context), design.md § Authentication and events and § Boundaries, proposal § What Changes (bullets 1-3).

## Current state (verified)

- `app/api/` has only `/healthcheck`; `app/main.py` includes `app.api.router`. API tests use `fastapi.testclient.TestClient(app)` (`tests/test_healthcheck.py`).
- Phase 3 seams to reuse, unchanged:
  - `app.ingestion.VcsEvent` (frozen dataclass): `provider`, `delivery_id`, `repository_external_id` (GitHub `owner/name`, GitLab numeric project id as text), `pr_number`, `action`, `head_sha`, optional `base_sha`, `repository_name`, `repository_url`, `title`, `author`, `source_branch`, `target_branch`.
  - `accept_delivery(session, event) -> AcceptResult(outcome: CREATED | DUPLICATE_DELIVERY | DUPLICATE_REVISION, delivery_id)`; commits; PostgreSQL-only (`ON CONFLICT`).
  - `enqueue_delivery(factory, delivery_id, *, queue=None) -> DispatchOutcome`; never raises; a failure leaves the row for the `recovery` service.
  - Worker `app.worker.ingestion.run_ingestion`, with the `_runtime()` monkeypatch seam used by `tests/worker/test_ingestion_job.py`.
- Adapters already fix the repository identity: `GitHubClient` requires `owner/name`; `GitLabClient` requires a numeric project id. Phase 4 normalization must produce exactly these.
- Column limits that validation must respect (otherwise `accept_delivery` raises `DataError`): `delivery_id` 255, `repository_external_id` 255, `action` 64, `head_sha`/`base_sha` 64, `repository_name` 255, `repository_url` 2048, `title` 500, `author`/branches 255.
- Test infrastructure: PostgreSQL via `TEST_DATABASE_URL` (`tests/db/conftest.py::pg_engine`, `tests/ingestion/conftest.py::session_factory`), Redis via fakeredis or `TEST_REDIS_URL` (`redis_server`); CI `checks` provides both.
- `app.config.Settings` is a frozen dataclass loaded at import; tests override with `dataclasses.replace(load_settings(), ...)`.

Provider protocols (checked against current provider docs during planning, 2026-10):

- GitHub: `X-Hub-Signature-256: sha256=<hex HMAC-SHA256(secret, raw body)>`; `X-GitHub-Event`; `X-GitHub-Delivery` (GUID, identical on manual redelivery). Payloads may be JSON or form-encoded depending on the hook's content type; GitHub Apps always send JSON. Documented test vector: secret `It's a Secret to Everybody`, body `Hello, World!` -> `757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17`. Respond 2xx within 10 s; payloads capped at 25 MB.
- GitLab signed webhooks (GitLab 19.0+, GA 19.1; Standard Webhooks): headers `webhook-id`, `webhook-timestamp` (Unix seconds), `webhook-signature` (space-separated `v1,<base64>` entries). Signed content is `"{webhook-id}.{webhook-timestamp}.{raw body}"`; the key is the signing token with the `whsec_` prefix stripped, base64-decoded. Compare in constant time against every entry. Docs recommend validating timestamp freshness against replay.
- GitLab secret token: `X-Gitlab-Token` sent as plain text. `X-Gitlab-Event: Merge Request Hook`. `Idempotency-Key` (17.4+) and `webhook-id` (19.0+) are equal and stable across retries. `object_attributes.action` values include `open`, `update`, `reopen`, `close`, `merge`, `approval(s)`; `object_attributes.oldrev` is present only for `update` with code changes (push, applied suggestion). Head is `object_attributes.last_commit.id`. GitLab recommends responding 200/201 quickly and returning 4xx only for misconfigured webhooks; four consecutive failures auto-disable the hook.

## Architecture Decisions

- Layering (design.md § Boundaries): new framework-free package `app/webhooks/` holds authentication (pure functions over `bytes` + header mapping) and provider payload models/normalization; `app/api/webhooks.py` is a thin router that reads raw bytes, calls these functions in a fixed order, calls one ingestion service function, and maps outcomes to HTTP. No FastAPI imports in `app/webhooks/`; no SQLAlchemy/RQ in the router beyond dependency wiring.
- Request order is fixed and tested: bounded body read -> authenticate raw bytes -> route by event header (unrelated: acknowledge) -> parse JSON -> check action (unsupported: acknowledge) -> validate supported payload -> normalize -> durable acceptance. Nothing is parsed before authentication; authentication covers unrelated events as well.
- GitHub authentication: HMAC-SHA256 hex over the raw bytes, header must be `sha256=` plus 64 hex chars, `hmac.compare_digest`. No SHA-1 `X-Hub-Signature` fallback.
- GitLab authentication mode is explicit configuration, `GITLAB_WEBHOOK_AUTH=signed|token`, default `signed`:
  - `signed`: all three `webhook-*` headers required; timestamp must be an integer within +/-300 s of now (module constant, Standard Webhooks default); any `v1,` entry must match. Missing/invalid signature is rejected even when a valid `X-Gitlab-Token` is present (no fallback).
  - `token`: constant-time comparison of `X-Gitlab-Token` with the configured secret token; signature headers are not consulted. No freshness check exists in this mode (the provider sends no signed timestamp); replay is bounded by delivery/revision deduplication.
  - Comparisons operate on `bytes` (`hmac.compare_digest` rejects non-ASCII `str`), so a non-ASCII header value is a rejection, not a 500.
- Fail closed: an empty secret, an undecodable `whsec_` token, or an unknown auth mode rejects every request with 401 and logs a configuration error once per request at ERROR without secret material. The API process still starts (healthcheck unaffected).
- New settings (secrets declared with `field(repr=False)`): `GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_AUTH` (default `signed`), `GITLAB_WEBHOOK_SIGNING_TOKEN`, `GITLAB_WEBHOOK_SECRET_TOKEN`. Freshness tolerance is a constant, not a setting.
- Supported events and normalization:
  - GitHub: `X-GitHub-Event: pull_request` with `action` in {`opened`, `synchronize`}. Fields: `number`; `pull_request.head.sha`, `.base.sha`, `.head.ref`, `.base.ref`, `.title`, `.user.login`; `repository.full_name` (-> `repository_external_id` and `repository_name`), `repository.html_url`. `delivery_id` = `X-GitHub-Delivery`.
  - GitLab: `X-Gitlab-Event: Merge Request Hook`, body `object_kind == "merge_request"`; `action == "open"`, or `action == "update"` with a non-empty `oldrev` different from `last_commit.id` (source-commit update). Fields: `project.id` (-> text `repository_external_id`), `project.path_with_namespace`, `project.web_url`, `object_attributes.iid`, `.last_commit.id`, `.title`, `.source_branch`, `.target_branch`. `base_sha` is `None` (the payload has no diff refs; the adapter supplies it). `author` = `user.username` only when `user.id == object_attributes.author_id` (the top-level `user` is the actor, not necessarily the author), otherwise `None`. `delivery_id` = `webhook-id`, else `Idempotency-Key`; neither present is an invalid payload (see Open Questions).
  - SHAs are normalized to lowercase and must match `^[0-9a-f]{40}([0-9a-f]{24})?$` (SHA-1 or SHA-256 repositories). PR/MR numbers and project ids are strict positive integers (no bools/strings). `full_name` must be exactly two non-empty segments, neither `.` nor `..` (same rule as `GitHubClient`). String lengths are bounded by the column limits above.
  - Everything else authenticated (GitHub `ping`, other events/actions, GitLab `reopen`/`close`/`merge`/approvals, `update` without `oldrev`, other hook types) is acknowledged as ignored with no inbox row.
- Normalization result type: `VcsEvent | IgnoredEvent(reason: str)`; invalid supported payloads raise `WebhookPayloadError`; authentication failures raise `WebhookAuthError`. Reasons are logged; they are not echoed to the caller beyond the status string.
- HTTP semantics (all acknowledged outcomes use 200 because GitLab recommends 200/201 and GitHub accepts any 2xx; the body is a small JSON for observability):

  | Situation | Status | Body |
  |---|---|---|
  | New durable delivery (enqueued or enqueue failed) | 200 | `{"status": "accepted"}` |
  | Duplicate delivery id or duplicate source revision | 200 | `{"status": "duplicate"}` |
  | Authenticated unrelated event/action | 200 | `{"status": "ignored"}` |
  | Invalid supported payload (not JSON, not an object, missing/invalid field, missing delivery id) | 400 | generic detail |
  | Authentication failed or endpoint not configured | 401 | generic detail, no reason |
  | Body above 25 MiB (`Content-Length` or streamed) | 413 | generic detail |
  | Database unavailable during acceptance (`OperationalError`) | 503 | generic detail; no 2xx without a durable row |

- Durable acceptance stays in `app/ingestion/`: add `accept_and_dispatch(factory, event, *, queue) -> AcceptResult` to `app/ingestion/dispatch.py` (opens a session, `accept_delivery`, closes it, then `enqueue_delivery` only for `CREATED`). Duplicates are never re-enqueued; stuck rows are the recovery service's job. A delivery that previously ended `failed` stays failed on redelivery (explicit reruns are out of scope per design.md).
- The router is `async` only to read the body (`request.stream()` with the 25 MiB cap); authentication, normalization and the blocking DB/Redis work run in `starlette.concurrency.run_in_threadpool`.
- Dependencies (overridable with `app.dependency_overrides`): `get_settings` (returns `app.config.settings`), `get_session_factory` (lazy import of `app.database.SessionLocal`), `get_webhook_queue` (lazily created, cached RQ queue whose Redis client has short `socket_connect_timeout`/`socket_timeout`, about 2 s, so a blackholed Redis cannot push the response past GitHub's 10 s limit). Importing `app.main` must not require DB, Redis, or network.
- Logging: log provider, event, action, outcome and the delivery id; never log signatures, tokens, or the body.

## Task 4.1: Provider authentication and payload normalization

Description: Add the webhook settings and the framework-free `app/webhooks/` package: GitHub and GitLab (signed and token mode) authentication over raw bytes, and provider payload validation/normalization into `VcsEvent` or `IgnoredEvent`. No HTTP endpoints and no database access in this task.

Acceptance criteria:
- GitHub: a body signed with the configured secret authenticates (including the documented test vector); a wrong secret, a tampered body (one byte), a missing header, a header without `sha256=`, a non-hex/short digest, a non-ASCII header value, or an empty configured secret all raise `WebhookAuthError`.
- GitLab `signed`: a valid signature within +/-300 s authenticates, including when the header carries several entries of which one matches; a stale or future timestamp beyond tolerance, a non-integer timestamp, a missing `webhook-id`/`webhook-timestamp`/`webhook-signature`, a signature over a different `webhook-id` or timestamp, a tampered body, a malformed or empty signing token, or a valid `X-Gitlab-Token` accompanying an invalid/missing signature all raise `WebhookAuthError`. `token`: matching `X-Gitlab-Token` authenticates; missing/wrong/non-ASCII token or an empty configured token is rejected; an unknown mode is rejected.
- Normalization: GitHub `opened`/`synchronize` and GitLab `open`/`update`-with-changed-`oldrev` produce the exact `VcsEvent` described in Architecture Decisions (identity formats accepted by `GitHubClient`/`GitLabClient`, lowercase SHAs, delivery id from the right header, GitLab author rule); ignored events/actions return `IgnoredEvent`; invalid supported payloads (malformed JSON, non-object, missing/ill-typed fields, bad SHA, bad `full_name`, non-positive number, over-length strings, missing delivery id) raise `WebhookPayloadError`. An unsupported action with an otherwise invalid body is ignored, not rejected.

Verification:
- `rtk uv run pytest tests/webhooks tests/test_config.py -q`
- `rtk uv run ruff check app tests && rtk uv run ruff format --check app tests`
- `rtk uv run mypy app tests`, `rtk uv run pylint app` (or `rtk uv run pre-commit run --all-files`)
- Full suite `rtk uv run pytest -q`

Test seams:
- `app.webhooks.auth.verify_github(secret: str, body: bytes, headers: Mapping[str, str]) -> None` (raises `WebhookAuthError`).
- `app.webhooks.auth.verify_gitlab(mode: str, *, signing_token: str, secret_token: str, body: bytes, headers: Mapping[str, str], now: float) -> None`; `now` is injected so freshness is tested without sleeping or patching time.
- `app.webhooks.github.normalize_github(headers, body: bytes) -> VcsEvent | IgnoredEvent` and `app.webhooks.gitlab.normalize_gitlab(headers, body: bytes) -> VcsEvent | IgnoredEvent` (raise `WebhookPayloadError`). Header lookup must be case-insensitive; tests pass a plain dict with provider casing and a lower-cased variant.
- `app.config.load_settings()` for the new variables and defaults (`monkeypatch.setenv`/`delenv`), and `repr(settings)` not containing the secret values.
- Expected signatures in tests are computed independently from the provider docs (`hmac`/`base64` in the test, plus the GitHub documented vector), never by calling the production verifier's own helper, so the tests are not tautological.
- Provider-shaped fixtures `tests/webhooks/fixtures/*.json` (GitHub `pull_request` opened/synchronize/closed, `ping`; GitLab MR open/update-with-oldrev/update-without-oldrev/close) loaded as raw bytes; phase 5 may reuse them.

Dependencies: Phase 3 (`VcsEvent`); no database or Redis.

Files likely touched:
- `app/config.py`, `.env.example`, `tests/test_config.py`
- `app/webhooks/__init__.py`, `app/webhooks/errors.py`, `app/webhooks/auth.py`, `app/webhooks/github.py`, `app/webhooks/gitlab.py`
- `tests/webhooks/test_auth.py`, `tests/webhooks/test_github_events.py`, `tests/webhooks/test_gitlab_events.py`, `tests/webhooks/fixtures/*.json`

Estimated scope: Large by file count (about 11 files plus fixtures), but every module is small and pure. Kept as one checkbox because tasks.md defines it; the implementer must not add HTTP or DB code here and should do auth first, then normalization, each test-first.

## Checkpoint: after 4.1

- [ ] Auth and normalization tests pass; full suite, Ruff, mypy, pylint clean.
- [ ] No secret value appears in any exception message, log record, or `repr(settings)` (asserted in tests).

## Task 4.2: Webhook endpoints connected to durable acceptance

Description: Add `POST /webhooks/github` and `POST /webhooks/gitlab` in `app/api/webhooks.py`, the overridable dependencies, the bounded body read, and `accept_and_dispatch` in `app/ingestion/dispatch.py`; map outcomes to the HTTP table above and verify the end-to-end path to a persisted Context Level 1 through API tests.

Acceptance criteria:
- Invalid or missing authentication returns 401 for both providers and for unrelated event types, and creates no `VcsDelivery`, `ReviewJob`, or RQ job (spec: Authenticated delivery). Authenticated unrelated events/actions return 200 `ignored` with no row (spec: Supported source events). Invalid supported payloads return 400 with no row; an oversized body returns 413 without being authenticated or parsed.
- A supported delivery returns 200 `accepted` only after the inbox row is committed, and enqueues exactly one `ingest-<id>` RQ job; repeating the same delivery id, or sending a new delivery id for the same source revision, returns 200 `duplicate` with one row and no extra RQ job; a queue outage still returns 200 `accepted` with the row `pending` and `last_dispatched_at` unset; a database outage returns 503.
- For both providers, a signed provider-shaped delivery followed by a burst worker run yields a `ReviewJob` bound to the delivery head and a `ContextPayload` with the raw diff, structured context (files/hunks/lines as parsed by `parse_diff`) and provider metadata (spec: Persisted context), using the real `GitHubClient`/`GitLabClient` against `httpx.MockTransport`, so normalized identities are proven compatible with the adapters.

Verification:
- `rtk uv run pytest tests/api tests/test_healthcheck.py tests/worker -q` with `TEST_DATABASE_URL` set (throwaway PostgreSQL container, as in phase 3) and fakeredis; `TEST_REDIS_URL` optional locally, set in CI.
- Ruff check/format, `rtk uv run mypy app tests`, pylint (or `rtk uv run pre-commit run --all-files`), full suite.
- Manual check: `docker compose up` and `curl -i -X POST localhost:8000/webhooks/github` without a signature returns 401 (no secret configured -> fail closed).

Test seams:
- HTTP: `TestClient(app)` with `app.dependency_overrides` for `get_settings` (test secrets, GitLab mode), `get_session_factory` (phase 3 PostgreSQL `session_factory` fixture), and `get_webhook_queue` (fakeredis-backed `Queue`, or a stub queue whose `enqueue` raises for the outage case). Overrides are reset per test.
- Auth-failure, ignored-event, 400 and 413 tests need no PostgreSQL: override `get_session_factory` with a factory that fails the test if called, proving nothing is persisted before authentication/validation. The 503 case overrides it with a factory whose session raises `sqlalchemy.exc.OperationalError`.
- Service: `app.ingestion.dispatch.accept_and_dispatch(factory, event, *, queue) -> AcceptResult` tested directly on PostgreSQL for created/duplicate/enqueue-failure outcomes.
- End-to-end: after the POST, run `rq.SimpleWorker([queue], connection=queue.connection).work(burst=True)` with `app.worker.ingestion._runtime` monkeypatched (existing `use_runtime` pattern) to a `Runtime` whose client dispatches by provider to `GitHubClient`/`GitLabClient` built over `httpx.MockTransport` serving metadata and a fixture diff; assert DB state, not timing.
- Import safety: extend the existing subprocess import test (unreachable `REDIS_URL`/`POSTGRES_HOST`) to `import app.main`.
- Signed requests are produced by a small test helper (`tests/api/conftest.py`) that signs fixture bytes per provider protocol and sends the exact bytes (`content=`), never re-serialized JSON.

Dependencies: 4.1; phase 3 ingestion, dispatch and worker.

Files likely touched:
- `app/api/webhooks.py`, `app/api/__init__.py`
- `app/ingestion/dispatch.py` (and `app/ingestion/__init__.py` export if useful)
- `tests/api/conftest.py`, `tests/api/test_webhooks.py`, `tests/api/test_webhook_ingestion_flow.py`, `tests/ingestion/test_dispatch.py` (accept_and_dispatch cases), `tests/worker/test_ingestion_job.py` (import test only)

Estimated scope: Medium to Large (about 8 files, mostly tests).

## Checkpoint: Phase 4 complete

- [ ] `rtk uv run pytest -q` with PostgreSQL and Redis, Ruff check/format, mypy, pylint clean; PostgreSQL-backed API tests are not skipped in CI (`TEST_DATABASE_URL` already set in the `checks` job).
- [ ] Spec scenarios Authenticated delivery, Supported source events, Durable idempotent acceptance (API-level repeat) and Persisted context each have at least one API test.
- [ ] `openspec validate add-vcs-webhooks-context --strict` passes; commit mode: after each task.
- [ ] Review with the user before planning phase 5.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Parsing or DB access before authentication | High | Fixed request order; tests with a factory that fails if called and with malformed bodies under a bad signature (expect 401, not 400). |
| Empty secret makes HMAC forgeable | High | Fail closed on empty/undecodable secrets and unknown mode; explicit tests. |
| Signature computed over re-serialized JSON instead of raw bytes | High | Verifiers take `bytes`; test helper sends exact fixture bytes; one-byte tamper and whitespace-variation tests. |
| GitLab signed-mode fallback to token weakens auth | High | Mode is explicit; test that a valid token with a bad/missing signature is rejected in `signed` mode. |
| Timing/encoding pitfalls (`compare_digest` on non-ASCII `str` raises) | Med | Compare bytes; non-ASCII header test expects 401. |
| Identity mismatch between normalization and adapters (`owner/name`, numeric project id) | High | End-to-end tests use the real adapters over `MockTransport`. |
| GitLab `update` without code changes creating work | Med | Require non-empty `oldrev` different from `last_commit.id`; fixture for label/title updates is ignored. |
| Slow Redis/DB pushing responses past GitHub's 10 s | Med | Redis client timeouts in the webhook queue dependency; enqueue failures never fail the response; DB `OperationalError` -> 503. |
| Unbounded body read before authentication | Med | 25 MiB cap via `Content-Length` and streaming; 413 test. |
| Column overflow (`DataError`) turning a bad payload into a 500 | Low | Length limits in payload validation; over-length test returns 400. |
| GitHub repository rename changes `owner/name`, creating a new `Repository` row and a new revision key | Low | Follows the existing phase 2/3 contract; recorded here, not changed in this phase. |

## Open Questions

- GitLab delivery identity without `webhook-id`/`Idempotency-Key` (GitLab older than 17.4): default in this plan is to reject with 400 as an invalid payload. Alternative: fall back to `X-Gitlab-Event-UUID`, whose stability across retries is not documented (revision deduplication would still prevent duplicate jobs). Confirm the default.
- HTTP semantics: default is 200 for accepted/duplicate/ignored (GitLab recommends 200/201) and 401 for an unconfigured endpoint. Alternatives: 202 for accepted, 503 for unconfigured. Confirm the default.
- GitHub form-encoded deliveries (`application/x-www-form-urlencoded`): default is unsupported (400 after authentication); phase 5 documentation must require `application/json`. Confirm.
- Out of phase 4 scope, for phase 5: the production deploy pipeline (`.github/workflows/ci-cd.yml` -> `infra/`) passes no VCS tokens or webhook secrets, so a deployed backend would reject every webhook (fail closed) and the worker could not fetch diffs. The public URL behind Caddy will be `https://<host>/api/webhooks/<provider>`. Decide whether deploy wiring belongs to 5.2 or a separate change.

Resolved by the user: GitLab deliveries without `webhook-id`/`Idempotency-Key` are rejected with 400; HTTP semantics as planned (200 for accepted/duplicate/ignored, 401 for an unconfigured endpoint); deploy wiring of VCS tokens and webhook secrets belongs to task 5.2. JSON-only GitHub deliveries kept as the plan default.
