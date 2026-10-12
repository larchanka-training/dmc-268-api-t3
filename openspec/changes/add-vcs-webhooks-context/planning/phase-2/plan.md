# Implementation Plan: add-vcs-webhooks-context, Phase 2 (VCS adapters)

## Overview

Implement tasks 2.1 (GitHub) and 2.2 (GitLab) only. This phase delivers a synchronous, provider-neutral retrieval port plus two HTTP adapters that return PR/MR metadata and the complete raw unified diff, bound to one source/base revision. No webhook endpoints, database, migrations, queue, or parser changes: the adapters return raw text and the caller (phase 3) feeds it to `parse_diff`. OpenSpec `tasks.md` owns status.

Spec anchors: `specs/vcs-retrieval/spec.md` (bounded retrieval, revision and completeness) and design.md § VCS retrieval.

## Architecture Decisions

- New package `app/vcs/`, importable without network side effects; clients take an injected `httpx.Client` (same pattern as `app/llm/providers/eurorouter_provider.py`, tested with `httpx.MockTransport`).
- Port (`Protocol`) `VcsClient.fetch_revision(ref) -> RevisionSnapshot`. `PullRequestRef` carries provider, repository identity (GitHub `owner/name`; GitLab numeric project id), PR/MR number, and the `expected_head_sha` from the webhook. `RevisionSnapshot` carries normalized metadata (title, head_sha, base_sha, state, and provider-specific refs needed for persistence) and `raw_diff: str`.
- Trusted origins only: URLs are built from configured API base URLs plus path segments; webhook-provided URLs never reach the client. Redirects are not followed. Repository/project identifiers are URL-quoted.
- Revision consistency: fetch metadata, fetch diff, re-fetch metadata; head/base identifiers must equal the first read and the `expected_head_sha`, otherwise `VcsRevisionChangedError`. A mismatch with `expected_head_sha` means a newer delivery supersedes this one.
- Size limit is 32,000 tokens (user decision), measured with the existing `app.llm.tokens.approximate_token_count`. Streaming aborts at a hard byte ceiling of `max_tokens * 4 * 4` (4 chars/token, up to 4 UTF-8 bytes/char) so memory stays bounded; after decoding, `approximate_token_count(raw_diff) > 32000` raises `VcsDiffTooLargeError`. An over-limit diff is rejected whole, never truncated.
- Completeness: the size limit above applies and exceeding it raises a too-large error. Provider truncation/overflow indicators raise `VcsDiffIncompleteError`; a truncated diff is never returned. An empty diff is valid.
- Typed errors in `app/vcs/errors.py` with a `retryable` flag: auth (401/403 non-throttle), not found (404), rate limited (429 / throttle 403, with retry-after when present), revision changed, diff incomplete/too large, upstream unavailable (5xx, timeouts, transport errors, malformed bodies).
- Config additions only for retrieval: GitHub/GitLab API base URL, token, request timeout, `vcs_max_diff_tokens` (default 32000). Webhook secrets belong to phase 4.
- Tokens must never appear in error messages or logs.

## Task 2.1: GitHub adapter and retrieval foundation

Description: Create the port, models, error hierarchy, bounded-fetch helper, configuration, and the GitHub adapter (`GET /repos/{owner}/{repo}/pulls/{n}` for metadata, the same endpoint with the `application/vnd.github.v3.diff` media type for the raw diff).

Acceptance criteria:
- Metadata and diff are retrieved with `Authorization` and API-version headers from the configured origin only; redirects are rejected.
- Head/base changing between the two metadata reads, or differing from `expected_head_sha`, raises `VcsRevisionChangedError`.
- 401/403 -> auth, 404 -> not found, 429/rate-limit 403 -> rate limited, 5xx/timeout/transport error/invalid JSON -> upstream; provider "diff too large" responses and diffs above 32,000 approximate tokens raise a non-retryable too-large/incomplete error, never partial text.
- Empty diff returns `raw_diff == ""`; non-UTF-8 body is an explicit error.

Verification: `rtk uv run pytest tests/vcs tests/test_config.py -q` (httpx.MockTransport contract tests), Ruff check/format, `rtk uv run mypy app/vcs app/config.py`, full suite.

Dependencies: Phase 1 (done); no code dependency on the parser.

Files likely touched: app/vcs/__init__.py, errors.py, models.py, http.py, github.py, app/config.py, .env.example, tests/vcs/test_github.py, tests/test_config.py.

Estimated scope: Large for one task (about 9 files). It is kept as one checkbox because the shared pieces have no consumer other than the first adapter; the subagent should keep helpers minimal.

## Task 2.2: GitLab adapter

Description: Implement the GitLab adapter against the port from 2.1: `GET /projects/{id}/merge_requests/{iid}` for metadata (`sha`, `diff_refs.base_sha/head_sha/start_sha`, `changes_count`) and `GET /projects/{id}/merge_requests/{iid}/raw_diffs` for the raw diff.

Acceptance criteria:
- Uses `PRIVATE-TOKEN` (or configured equivalent) against the configured origin only; shares the error mapping and bounded-fetch helper.
- Revision check uses `sha` and `diff_refs` before and after the diff read plus `expected_head_sha`.
- Overflow is detected and rejected: `changes_count` with a `+` suffix, and any provider overflow/too-large flag or file-count mismatch between metadata and the diff's `diff --git` headers (where the endpoint exposes the signal). A diff that cannot be proven complete is an error.
- Pagination behavior is covered if the chosen endpoint paginates; otherwise the test documents that it does not.

Verification: `rtk uv run pytest tests/vcs -q`, Ruff, mypy, full suite.

Dependencies: 2.1.

Files likely touched: app/vcs/gitlab.py, app/vcs/models.py (only if metadata needs a field), tests/vcs/test_gitlab.py, tests/test_config.py.

Estimated scope: Medium.

## Checkpoint: Phase 2 complete

- [x] `rtk uv run pytest -q`, Ruff check/format, `mypy app tests` clean.
- [x] Both adapters satisfy the same `VcsClient` contract tests (shared parametrized cases where practical).
- [x] No test or code path performs real network I/O; no secret appears in error text.
- [x] `openspec validate add-vcs-webhooks-context --strict` passes; commit mode: after each task.
- [x] Review with the user before planning phase 3.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Provider diff-limit/overflow signals differ from assumptions (GitHub 406/422 on large diffs; GitLab `changes_count` "1000+", instance diff limits omitting files) | High | Subagent verifies each signal against current provider docs before coding and cites it in the test names; unknown signals fail closed as incomplete. |
| GitLab `raw_diffs` unavailable on older self-managed versions | Med | Document minimum version in config docs (phase 5); map 404-on-endpoint distinctly from missing MR only if the docs allow; otherwise report as an open question. |
| Base revision semantics: GitHub `base.sha` is branch tip, diff is against merge-base | Med | Persist both; revision check compares identifiers, not diff content. |
| Streaming size cap implemented incorrectly yields partial text | High | Test a diff just under and just over 32,000 approximate tokens (128,000 chars), a multi-byte body, and chunked transport. |
| Secrets leaking in exceptions | Med | Tests assert tokens are absent from `str(error)`. |

## Open Questions

- Should rate-limited and upstream errors be retried inside the adapter, or only surfaced to the phase 3 worker retry policy? Default in this plan: surface only, no internal retries.
- GitLab project identity for persistence: numeric project id (planned) versus `path_with_namespace`; confirm with phase 3.1 schema when planned.

Resolved by the user: adapters surface errors without internal retries (worker retries in phase 3); diff limit is 32k tokens.

## Outcome notes (carry into phase 3)

- GitHub: any 406/422 on the diff request is treated as incomplete (provider signal not documented). `diff --git` count is checked against `changed_files`.
- GitHub `base.sha` is the branch tip; base moving mid-fetch raises non-retryable `VcsRevisionChangedError`. Worker policy decides on re-enqueue.
- GitLab `raw_diffs` has no pagination or overflow flag; completeness relies on `changes_count` ("1000+" rejected) versus the `diff --git` header count.
- GitLab empty `changes_count` or unpopulated `diff_refs` on a new MR: `changes_count` problems are non-retryable `VcsDiffIncompleteError`; missing `diff_refs` is retryable `VcsUpstreamError`. Phase 3 may want the empty `changes_count` case retryable.
