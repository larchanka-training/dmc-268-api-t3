# Design

## Boundaries

HTTP handlers authenticate raw bytes and validate provider payloads. Application services normalize source events and orchestrate ingestion through ports. VCS HTTP and SQLAlchemy/RQ implementations are adapters. No network operations occur at import time. Typed delivery state and repository/queue ports live in `app/ingestion/ports.py`; application services own transitions and retry policy. SQLAlchemy persistence lives in `app/repositories/ingestion.py`, RQ dispatch in `app/worker/ingestion_queue.py`, and the only wiring of repository and queue adapters to the services (session-factory entry points) in `app/composition/ingestion.py`, importable by both API and worker. `app/ingestion/__init__.py` exports event/result types only. Claim and completion retain their separate short transactions and lease guards.

## Context Level 1

Use versioned Pydantic schemas for files, hunks, and lines. A line carries kind, content, nullable old/new numbers, and final-newline metadata. A file carries old/new paths and change type. Excluded files retain paths and a reason but no reviewable hunks. Store raw diff plus structured JSON and provider metadata in ContextPayload through a migration. Malformed, combined, or incomplete patches fail explicitly; an empty diff is valid. Exclude binaries, known lockfile basenames, and *.min.js / *.min.mjs; do not infer generated status from arbitrary file size.

## VCS retrieval

Use configured API origins and credentials, never webhook-supplied diff URLs. Fetch metadata and raw diff with bounded HTTP calls. Check head/base revisions before and after retrieval and reject changes during the fetch. GitLab uses MR raw diffs; detect overflow/too-large indicators. Never represent provider truncation as complete context. Errors distinguish authentication, missing resources, throttling, revision races, and unavailable upstreams.

## Authentication and events

GitHub uses X-Hub-Signature-256 over raw bytes. GitLab signed webhooks use its documented signing protocol with freshness validation; secret-token mode is explicitly configured for older installations. Invalid signed requests cannot fall back. Supported actions are GitHub opened/synchronize and GitLab open/update with a changed source revision. Authenticated unrelated events create no jobs.

## Durable acceptance and recovery

Persist a delivery inbox before returning acceptance. Unique provider delivery identity deduplicates transport retries; provider/repository/PR/head identity deduplicates source revisions across deliveries. Explicit reruns are outside this change. An importable ingestion worker claims durable records, downloads and parses context, and commits the job/context atomically. A recovery dispatcher re-enqueues unprocessed records after DB-to-Redis failures; duplicate dispatch is harmless. Use database uniqueness and locking, not check-then-insert alone. Bounded retries end in recorded failures, with no partial context.

## Verification and deployment

Unit and API tests use signed fixtures and HTTP mocks. PostgreSQL tests cover concurrent deduplication, migration, and transactional recovery; Redis/RQ integration covers dispatch recovery. A local replay script supports CI. The compose smoke uses locally signed provider fixtures and a configured stub VCS upstream; live provider evidence is recorded separately. The GitHub bot is a dedicated machine-user account added to the team repository as a collaborator with Read access, not a GitHub App: only a user account can be requested as a PR reviewer, and the adapter already reads a static `GITHUB_TOKEN`, while App installation tokens expire hourly and need a JWT exchange that this change does not implement. Its token is fine-grained for organization repositories (Contents and Metadata: Read; Pull requests and Issues: Read and write, needed later for the summary comment) or classic (`repo`, or `public_repo` for a public repository) for a repository in a personal account, where fine-grained tokens cannot reach a collaborator's repository. ngrok exposure is optional and local only: each developer keeps a personal static domain in `NGROK_DOMAIN` in `.env`; an opt-in `tunnel` docker compose profile forwards `api:8000` with `NGROK_AUTHTOKEN`, plain `docker compose up` is unchanged, and a host `ngrok` command on the same domain serves the API run with uvicorn. Document bot installation, credentials, the webhook subscription (JSON payload, Pull requests events only), and both tunnel options. A real provider smoke check remains separately reported and requires account access and a reachable endpoint.
