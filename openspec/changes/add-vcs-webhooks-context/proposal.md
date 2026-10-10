# Add VCS Webhooks and Context Level 1

## Why

The backend has review persistence models but cannot ingest GitHub pull requests or GitLab merge requests. Context Level 1 must turn an authenticated event into a persisted, revision-bound diff structure suitable for a subsequent review task.

## What Changes

- Add `POST /webhooks/github` and `POST /webhooks/gitlab`. Authenticate original request bytes before parsing. Verify GitHub HMAC-SHA256 and GitLab signed webhook HMAC using provider protocols. Support GitLab secret-token authentication explicitly for installations without signed webhooks; never fall back after an invalid signature.
- Handle GitHub `pull_request` actions `opened` and `synchronize`, and GitLab merge-request `open` and source-commit `update` events. Acknowledge unrelated authenticated events without creating work; reject invalid supported payloads.
- Normalize provider-scoped repository identity, PR/MR number, delivery identity, and source/base revisions.
- Add typed VCS API adapters for metadata and raw unified diff, using configured credentials, trusted API base URLs, timeouts, size limits, and explicit upstream errors. Bind metadata and diff to the same revision; detect concurrent updates and truncation instead of silently saving incomplete context.
- Parse files, change types, old/new paths, hunks, line kinds, old/new line numbers, and no-final-newline markers. Cover additions, deletions, renames, quoted paths, multiple hunks, and empty diffs. Record exclusions for binary files, lock files, and minified JavaScript. Reject malformed or unsupported patches explicitly.
- Persist metadata, raw diff, structured Context Level 1, and revision identity through the existing entities and necessary migrations. Use durable, idempotent delivery tracking and the RQ worker for ingestion; recover retryable failures without duplicate review jobs or lost accepted deliveries.
- Add configuration and webhook setup documentation, and tests for authentication, adapters, parser edge cases, persistence, redelivery, and failures. Update architecture documentation.
- Document GitHub bot/App setup and installation in a team repository, including credentials and webhook subscriptions. Treat the bot's reviewer assignment as deployment context; ingestion remains triggered by the supported PR events, without requiring a review request.
- Provide two verification paths: real GitHub deliveries through an optional ngrok tunnel to the local backend, and a local script that signs provider-shaped fixtures and posts them to the webhook endpoint. Run the reproducible script against the backend in CI and verify persisted context; distinguish simulated delivery from a live provider check.

## Impact

Affected areas: API, configuration, new VCS/context modules, orchestration, persistence, worker jobs, migrations, tests, and documentation. Capability deltas will cover webhook ingestion, VCS retrieval, and Context Level 1, with persistence extensions where needed.

Acceptance: an authentic delivery to a configured backend produces revision-bound structured context in the database. Automated tests use provider-shaped payloads and mocked upstream APIs. A live check requires a reachable webhook URL and provider credentials.

The supplied deployment context describes a GitHub bot added to a team repository and its reviewers. Optional ngrok routing exposes the local webhook endpoint for real deliveries (https://ngrok.com/). A signed local replay script supports development and CI without a public tunnel. External bot registration, repository installation, and reviewer configuration require account access and are documented setup steps; this proposal does not assume they are already completed.

## Non-goals

LLM review execution, publishing comments, Context Levels 2/3, repository cloning, and UI changes.
