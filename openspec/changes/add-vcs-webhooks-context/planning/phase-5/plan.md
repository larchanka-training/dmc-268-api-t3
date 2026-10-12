# Implementation Plan: add-vcs-webhooks-context, Phase 5 (Acceptance and documentation)

## Overview

Plan the open tasks 5.1, 5.3 and 5.4. Task 5.2 (ngrok `tunnel` profile, commit 6af2819) is done and is out of scope. 5.1 adds a signed replay script and a CI smoke run against the real compose stack, with a stub VCS upstream, so CI proves webhook -> inbox -> worker -> persisted Context Level 1 and rejects bad authentication. 5.3 wires the webhook secrets, VCS tokens, and the recovery dispatcher into production (the user assigned deploy wiring to "the docs/config task"). It also documents bot setup, token permissions, configuration and architecture, and reports whether a live provider check is possible. 5.4 runs every check, reconciles the artifacts with the code, and hands archival and the PR to the parent. OpenSpec `tasks.md` owns status.

Spec anchors: `specs/webhook-ingestion/spec.md` (all five requirements), `specs/vcs-retrieval/spec.md`, `specs/context-level-one/spec.md`; design.md § Verification and deployment; proposal bullets 7-9 and § Impact ("Acceptance").

## Current state (verified 2026-10-12)

- Endpoints: `app/api/webhooks.py`, router prefix `/webhooks` -> locally `http://localhost:8000/webhooks/{github,gitlab}`. In production Caddy `handle_path /api/*` strips `/api` and uvicorn runs with `--root-path /api`, so the public URL is `https://<SITE_ADDRESS>/api/webhooks/{github,gitlab}`. Over ngrok it is `https://<NGROK_DOMAIN>/webhooks/github`. Responses: 200 `accepted`/`duplicate`/`ignored`, 400 invalid payload (form-encoded GitHub bodies included), 401 auth/unconfigured, 413 > 25 MiB, 503 DB unavailable.
- Settings (`app/config.py`, `.env.example`): `GITHUB_TOKEN`, `GITHUB_API_BASE_URL`, `GITLAB_TOKEN`, `GITLAB_API_BASE_URL`, `VCS_TIMEOUT_SECONDS`, `VCS_MAX_DIFF_TOKENS`, `RECOVERY_INTERVAL_SECONDS`, `RECOVERY_STALE_AFTER_SECONDS`, `GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_AUTH` (`signed`|`token`), `GITLAB_WEBHOOK_SIGNING_TOKEN` (`whsec_<base64>`), `GITLAB_WEBHOOK_SECRET_TOKEN`. An empty secret means fail closed (401). An empty VCS token makes the delivery fail terminally with `last_error` (`SettingsVcsClient` raises `VcsInvalidRequestError`).
- Adapters accept `http://` base URLs (`GitHubClient`/`GitLabClient` check `https://` or `http://`). That allows a plain-HTTP stub upstream in CI.
- Compose (`docker-compose.yml`): `api`, `worker`, `recovery` (`python -m app.worker.recovery`), `migrate`, `postgres` (published 5432, user/db `app`), `redis`, opt-in `ngrok`. `.env` is optional.
- Production (`infra/main.tf`): `local.app_env` has DB, Redis and Eurorouter only. There are **no VCS tokens, no webhook secrets, and no `recovery` container**. A deployed backend would therefore return 401 to every webhook, and accepted-but-undispatched deliveries would never be re-enqueued.
- CI (`.github/workflows/ci-cd.yml`): the `checks` job runs pre-commit and pytest with `TEST_DATABASE_URL`/`TEST_REDIS_URL`. The `compose` job runs `up --wait`, curls `/healthcheck`, and runs `python -m app.worker.smoke`. The deploy smoke curls `/api/healthcheck` and runs the worker smoke over SSH.
- Image: the Dockerfile copies only `alembic*` and `app/`, so a top-level `scripts/` directory is not shipped in production.
- Test helpers already exist: provider signing in `tests/api/conftest.py`, fixtures in `tests/webhooks/fixtures/` (GitHub `octo/repo#42`, GitLab project `123` `!7`, head `a*40`, GitHub base `b*40`), and the end-to-end flow with `MockTransport` handlers in `tests/api/test_webhook_ingestion_flow.py`.
- Tooling scope: mypy `files = ["app", "tests", "alembic"]`, pylint runs on `app` only, ruff runs on all Python files. `openspec validate add-vcs-webhooks-context --strict` passes.

## Architecture Decisions

- **Replay script location: `scripts/replay_webhook.py`, stdlib only** (`argparse`, `hmac`, `hashlib`, `base64`, `json`, `urllib.request`, `uuid`, `time`, `os`). It runs on any host with `python3` (the CI runner needs no `uv sync`). It is not shipped in the production image. Rejected alternative: a module under `app/` like `app/worker/smoke.py`, which would put a request-forging dev tool into production.
- **Secrets are never literals and never logged.** The script reads the signing value from an environment variable. By default this is the variable with the backend's name (`GITHUB_WEBHOOK_SECRET`, `GITLAB_WEBHOOK_SIGNING_TOKEN`, `GITLAB_WEBHOOK_SECRET_TOKEN`). `--secret-env NAME` picks another variable, which negative tests use. There is no raw `--secret VALUE` flag, which keeps secrets out of shell history and `ps`. Output contains only the HTTP status, the small JSON body and the delivery id, never signatures or secrets. Every run prints that it is a **simulated delivery signed locally, not a live provider check**.
- **Script CLI** (final names belong to the implementer, behaviour is fixed): `replay_webhook.py {github|gitlab} --url URL --payload FILE [--event NAME] [--delivery-id ID] [--gitlab-mode signed|token] [--secret-env NAME] [--unsigned] [--timestamp-offset SECONDS] [--expect-status N] [--expect STATUS]`. The script sends the file's exact bytes with `Content-Type: application/json`. The delivery id defaults to `uuid4`. Exit code 0 only when the response matches the expectations (default: status 200), 1 otherwise, 2 on usage or configuration errors (missing env var, unreadable file).
- **CI end-to-end needs a fake upstream, not real GitHub.** A stdlib `scripts/vcs_stub.py` (`ThreadingHTTPServer`) serves GitHub and GitLab metadata and raw diffs for the fixture identities only, and returns 404 for anything else. A CI-only compose override, `docker-compose.smoke.yml`, adds a `vcs-stub` service and overrides `api`/`worker`/`recovery` env. It sets `GITHUB_API_BASE_URL=http://vcs-stub:8080`, `GITLAB_API_BASE_URL=http://vcs-stub:8080/api/v4`, throwaway tokens and throwaway webhook secrets, and `GITLAB_WEBHOOK_AUTH=signed`. `vcs-stub` uses `build: .` (the same image, no extra pull) with `./scripts` mounted read-only. Plain `docker compose up` stays unchanged.
- **Smoke orchestration in one shell script, `scripts/smoke_webhooks.sh`**, so CI and a developer run exactly the same thing. It sends replays, checks responses, and polls PostgreSQL through `docker compose exec -T postgres psql -U app -d app -tAc ...`, which needs no host DB driver. CI sets `COMPOSE_FILE=docker-compose.yml:docker-compose.smoke.yml` at the `compose` job level, so `up`, `exec` and `logs` all see the override.
- **CI smoke cases** (each uses a unique delivery id):
  1. GitHub `opened`, signed -> 200 `accepted`. Then poll until the delivery is `succeeded` (60 s budget, 1 s interval). Assert a `context_payloads` row for `octo/repo#42`: `head_sha = a*40`, `base_sha = b*40`, non-empty `diff_text`, `schema_version` set, `structured_context -> 'files'` non-empty. On timeout, print `status`/`last_error` (no diff) and fail.
  2. The same delivery id again -> 200 `duplicate`. A new id for the same revision -> 200 `duplicate`. Still exactly one delivery row and one context for that revision.
  3. GitLab `open`, signed -> `accepted` -> `succeeded`, with context for project `123` `!7` (`base_sha` comes from the stub's `diff_refs`).
  4. Negative, each -> 401 with **zero** `vcs_deliveries` rows for its id: GitHub unsigned, GitHub signed with a wrong secret, GitLab signed with a timestamp 600 s in the past, and GitLab sending only `X-Gitlab-Token` to the signed-mode server (no fallback).
  5. GitHub `ping` signed -> 200 `ignored`, no row.

  GitLab token mode is not run in compose (the stack has one mode). It stays covered by the unit and API tests.
- **Throwaway smoke secrets** are literal, clearly labelled values in `docker-compose.smoke.yml` and in the `compose` job `env` (for example `smoke-github-secret-not-real`, `whsec_` + base64 of a fixed test key). They are not repository secrets and appear nowhere else.
- **Production wiring (5.3a).** Add Terraform variables (`sensitive = true`, default `""`) and `local.app_env` entries for the five secrets plus `GITLAB_WEBHOOK_AUTH` (default `signed`), and add a `recovery` container (same image and env, `python -m app.worker.recovery`, `restart = "unless-stopped"`). `ci-cd.yml` passes them in "Stage config and secrets", following the procedure in `DEVOPS.md` §4. GitHub forbids secret and variable names starting with `GITHUB_`, so the repository secrets need a prefix. Default proposal (see Open Questions): `VCS_GITHUB_TOKEN`, `VCS_GITHUB_WEBHOOK_SECRET`, `VCS_GITLAB_TOKEN`, `VCS_GITLAB_WEBHOOK_SIGNING_TOKEN`, `VCS_GITLAB_WEBHOOK_SECRET_TOKEN`, and the repository variable `VCS_GITLAB_WEBHOOK_AUTH`. The deploy does **not** require them (no `:?`): missing secrets keep the endpoint fail-closed (401), which the docs state. API base URLs keep their code defaults (github.com, gitlab.com). Wiring them is out of scope unless requested.
- **Deploy smoke extension (5.3a)**: an unsigned `POST https://${SITE_ADDRESS}/api/webhooks/github` must return 401 (proves routing through Caddy and fail-closed auth without any secret), and `docker inspect -f '{{.State.Running}}' dmc268-recovery` must print `true`.
- **Documentation home (5.3b)**: a new `docs/VCS_SETUP.md` for bot, tokens, webhook configuration, live check and residual risks. README, WORKFLOW, DEVOPS and AGENTS.md link to it instead of duplicating it. Architecture changes go to `BACKEND_ARCHITECTURE.md`, `docs/architecture/erd.md`, `SYSTEM_DESIGN.md` §5/§15 and `openspec/project.md` (5.3c).

## Task 5.1: Signed replay script and CI smoke coverage

5.1 is one checkbox in `tasks.md`, delivered as two sequential implementer runs. The first run (5.1a) is committed without ticking the box; the second (5.1b) ticks it. Run them in order (5.1b uses the script).

### 5.1a Replay script with unit tests

Description: add `scripts/__init__.py` (empty) and `scripts/replay_webhook.py` per Architecture Decisions, add `"scripts"` to `[tool.mypy] files` in `pyproject.toml` (tool config only, no dependency change), and add tests.

Acceptance criteria:
- Headers the script builds for GitHub, GitLab signed and GitLab token mode authenticate with the production verifiers. `--unsigned`, a wrong `--secret-env`, and a negative `--timestamp-offset` beyond 300 s are rejected by them. The GitHub signature matches the documented vector (`It's a Secret to Everybody` / `Hello, World!` -> `757107ea...3e17`).
- The body sent is byte-identical to the payload file. A missing env var or file exits 2 with a message that contains no secret. Expectation mismatch exits 1. Output never contains the secret, the signature, or the body.
- Running against the app (in process) with script-built headers: a signed GitHub `ping` -> 200 `ignored`, a wrong secret -> 401.

Verification:
- `uv run pytest tests/scripts -q`
- `uv run pre-commit run --all-files` (ruff and mypy now include `scripts/`)
- Manual: `python3 scripts/replay_webhook.py --help`

Test seams:
- Pure builders in the script, for example `build_github_headers(secret: str, body: bytes, *, event, delivery_id) -> dict[str, str]` and `build_gitlab_headers(mode, secret: str, body: bytes, *, event, delivery_id, now: float) -> dict[str, str]` with an injected `now`. They are checked against `app.webhooks.auth.verify_github`/`verify_gitlab` (independent code, so the test is not tautological) and against the GitHub documented vector.
- `main(argv: list[str], *, env: Mapping[str, str], send: Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]) -> int`. Tests inject `env` and a `send` that posts through `TestClient` with the `api` fixture from `tests/api/conftest.py` (fail-if-called DB factory, so `ping`/401 need no PostgreSQL). Captured stdout/stderr are asserted to contain no secret value.

Dependencies: none (phase 4 code).

Files: `scripts/__init__.py`, `scripts/replay_webhook.py`, `pyproject.toml` (mypy `files`), `tests/scripts/test_replay_webhook.py`.

Scope: Small/Medium.

### 5.1b Stub VCS upstream, smoke script, CI wiring

Description: add `scripts/vcs_stub.py`, `docker-compose.smoke.yml`, and `scripts/smoke_webhooks.sh`, and extend the `compose` job in `.github/workflows/ci-cd.yml` (`COMPOSE_FILE` at job level, test-only secret env, one step running `scripts/smoke_webhooks.sh` after the worker smoke; the existing `docker compose logs` on failure stays).

Acceptance criteria:
- The stub serves exactly what `GitHubClient`/`GitLabClient` request for the fixture identities (metadata twice, raw diff once; GitHub `changed_files` and GitLab `changes_count`/`diff_refs` consistent with a one-file diff) and returns 404 otherwise. The real adapters' `fetch_revision` succeeds against it.
- `scripts/smoke_webhooks.sh` passes against `docker compose up` with the override, covering cases 1-5 from Architecture Decisions, including DB-level proof of persisted context, deduplication, and zero rows for rejected deliveries. It exits non-zero with a short diagnostic (no diff, no secrets) on any mismatch.
- Plain `docker compose config` is unchanged by this task. The override validates. CI `compose` job is green.

Verification:
- `uv run pytest tests/scripts -q` (stub contract test)
- `docker compose config --quiet` and `docker compose -f docker-compose.yml -f docker-compose.smoke.yml config --quiet`
- `docker compose --profile tunnel config --quiet` (5.2 profile still valid)
- Locally: `COMPOSE_FILE=docker-compose.yml:docker-compose.smoke.yml docker compose up -d --build --wait --wait-timeout 180 && scripts/smoke_webhooks.sh; docker compose down -v`
- `uv run pre-commit run --all-files` (check-yaml covers the workflow and the override)
- CI: the `compose` job on the PR

Test seams:
- `scripts/vcs_stub.py` exposes `make_server(host: str, port: int) -> ThreadingHTTPServer`. The test starts it on `127.0.0.1:0` in a thread and calls the real `GitHubClient(f"http://127.0.0.1:{port}", "t")` and `GitLabClient(f"http://127.0.0.1:{port}/api/v4", "t")` `.fetch_revision(...)` for the fixture refs, then runs `parse_diff` on the result. This keeps the stub and adapters in sync inside the `checks` job, before the compose job runs. It also covers an unknown path -> 404.
- The shell smoke script itself is verified by running it (locally and in CI); it has no unit tests.

Dependencies: 5.1a.

Files: `scripts/vcs_stub.py`, `scripts/smoke_webhooks.sh` (executable), `docker-compose.smoke.yml`, `.github/workflows/ci-cd.yml`, `tests/scripts/test_vcs_stub.py`.

Scope: Medium.

### Checkpoint: after 5.1

- [ ] Full suite with `TEST_DATABASE_URL`/`TEST_REDIS_URL` (throwaway containers) and `uv run pre-commit run --all-files` pass.
- [ ] The local compose smoke with the override passes. The CI `compose` job is green on the pushed branch, or it is reported as not yet run.
- [ ] `grep` shows no real-looking secret in the repository. The smoke values are labelled as test-only.

## Task 5.3: Configuration wiring and documentation

One checkbox, delivered as three sequential runs: 5.3a, 5.3b, 5.3c. The box is ticked with 5.3c. 5.3a must precede 5.3b because the docs describe the wiring. 5.3a touches `ci-cd.yml`, so it runs after 5.1b.

### 5.3a Production wiring of VCS credentials, webhook secrets and recovery

Description: implement the production wiring from Architecture Decisions in `infra/variables.tf`, `infra/main.tf` and `.github/workflows/ci-cd.yml` (stage step and deploy smoke). Do not edit `deploy.sh` unless validation shows it is required.

Acceptance criteria:
- `local.app_env` contains `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GITLAB_TOKEN`, `GITLAB_WEBHOOK_AUTH`, `GITLAB_WEBHOOK_SIGNING_TOKEN` and `GITLAB_WEBHOOK_SECRET_TOKEN`, sourced from variables. Secret variables are `sensitive = true` with default `""`.
- `dmc268-recovery` exists (same image and env as the worker, `python -m app.worker.recovery`, `restart = "unless-stopped"`, depends on migrate and redis).
- The "Stage config and secrets" step passes the new values via `jq --arg` into `api.auto.tfvars.json` only (never as SSH arguments). The deploy smoke asserts 401 for an unsigned webhook POST and a running recovery container.
- Missing secrets do not fail the deploy. This behaviour is documented in 5.3b.

Verification:
- `infra/apply.sh fmt -check -diff`, `infra/apply.sh init -backend=false -input=false -no-color`, `infra/apply.sh validate -no-color` (needs Docker. If unavailable, say so; the CI `terraform` job is authoritative.)
- `uv run pre-commit run --all-files` (check-yaml)
- Manual review: no `${{ secrets.* }}` is interpolated into a `run:` script body. Secrets reach the script only through `env:`, matching the existing pattern.
- Live deploy happens only after the merge to `main`. Report it as unverified until then.

Test seams: None (infrastructure configuration, no application behaviour). Verified by Terraform validate and the deploy smoke.

Dependencies: 5.1b (same workflow file); user confirmation of the secret names and the recovery container (Open Questions).

Files: `infra/variables.tf`, `infra/main.tf`, `.github/workflows/ci-cd.yml`.

Scope: Small.

### 5.3b Setup and configuration documentation

Description: write `docs/VCS_SETUP.md`, and update `README.md` (environment table), `docs/WORKFLOW.md` (link from §2, the replay and smoke commands in §2/§3) and `docs/DEVOPS.md` (§1 container table and diagram with `dmc268-recovery`, §4 secrets table with the new secrets/variable, §6 handover container count, §8 limitations, §9 webhook troubleshooting). Every statement is checked against the code paths cited below.

`docs/VCS_SETUP.md` content:
1. GitHub bot (design.md): a dedicated machine-user account, added to the team repository as a collaborator with Read access, not a GitHub App, and why (reviewer requests need a user account; the adapter reads a static `GITHUB_TOKEN`; App tokens expire hourly and need a JWT exchange). Reviewer assignment is deployment context; ingestion triggers on `opened`/`synchronize` without a review request.
2. Token permissions: fine-grained token for organization repositories (Contents and Metadata: Read; Pull requests and Issues: Read and write, needed later for the summary comment), or a classic token (`repo`, or `public_repo` for a public repository) for a repository in a personal account. GitLab: the minimal token for MR metadata and raw diffs. Give only the scope the adapter needs (verify against `app/vcs/gitlab.py` endpoints). Do not invent a scope; if the minimal scope is uncertain, write that it is unverified and say so in the report.
3. Webhook configuration. GitHub: Payload URL `https://<SITE_ADDRESS>/api/webhooks/github` (production) or `https://<NGROK_DOMAIN>/webhooks/github` (local); **Content type `application/json`** (form-encoded gets 400); Secret = `GITHUB_WEBHOOK_SECRET`; events "Pull requests" only. GitLab: URL `.../webhooks/gitlab`; Merge request events; signed mode (signing token `whsec_...` -> `GITLAB_WEBHOOK_SIGNING_TOKEN`, GitLab 19.0+) or token mode (`GITLAB_WEBHOOK_AUTH=token`, secret token -> `GITLAB_WEBHOOK_SECRET_TOKEN`); a delivery without `webhook-id`/`Idempotency-Key` (GitLab < 17.4) gets 400.
4. Configuration table for every VCS/webhook/recovery variable with its default, its meaning, and the production secret/variable name from 5.3a. Mark provisional values (`VCS_MAX_DIFF_TOKENS`, recovery interval and staleness, the 300 s freshness and 25 MiB constants) as provisional per AGENTS.md.
5. Event matrix and response codes (from `app/webhooks/github.py`, `gitlab.py`, `app/api/webhooks.py`).
6. Verification paths: (a) simulated, which means `scripts/replay_webhook.py` and `scripts/smoke_webhooks.sh` (what they prove and what they do not); (b) live, which means a real PR on the team repository through ngrok or production, checked in GitHub "Recent Deliveries" and in the DB (`vcs_deliveries.status`, `context_payloads`). A live check needs the bot account, a token, a reachable URL and admin access to the repository settings.
7. Residual risks from phase 4: the recovery dispatcher must run in every environment (a 503 after a commit leads to provider redelivery -> `duplicate`, and the stored row is dispatched only by recovery); GitLab token mode compares the header decoded as latin-1 with the UTF-8 configured secret, so **use ASCII-only secrets**; `repository_url` is stored without scheme validation (display only, never fetched; adapters use configured API origins); a GitHub repository rename changes `owner/name` and therefore the revision key.

Acceptance criteria:
- Every URL, header, variable name, default, status code and event in the docs matches the code (a cross-check list is in the 5.3b report). No real secret, token or personal domain appears.
- The docs distinguish a simulated delivery from a live provider check. The 5.3b report states whether a live check was possible in this session (expected: no, it needs account access) and what the user must do to run it.
- Links resolve: README/WORKFLOW/DEVOPS link to `docs/VCS_SETUP.md`; one line is added to the AGENTS.md "Documentation" list.

Verification:
- `uv run pre-commit run --all-files` (whitespace and end-of-file on markdown)
- A manual cross-check of each documented fact against `app/config.py`, `.env.example`, `app/api/webhooks.py`, `app/webhooks/*`, `app/vcs/*`, `infra/Caddyfile`, `infra/main.tf`, `docker-compose*.yml` and `scripts/*`
- Optional: `python3 scripts/replay_webhook.py --help` output matches the documented flags

Test seams: None (documentation only).

Dependencies: 5.1, 5.3a.

Files: `docs/VCS_SETUP.md` (new), `README.md`, `docs/WORKFLOW.md`, `docs/DEVOPS.md`, `AGENTS.md` (one link line).

Scope: Medium (documentation).

### 5.3c Architecture documentation

Description: bring the architecture documents in line with the implemented code. Divergences outside this change are pointed out, not migrated.

- `docs/BACKEND_ARCHITECTURE.md`: replace "Only the data layer is implemented in this PR". Add the webhook -> ingestion flow (`app/api/webhooks.py` -> `app/webhooks/` auth and normalization -> `app/ingestion/` acceptance and dispatch -> RQ `app/worker/ingestion.py` -> `app/vcs/` adapters -> `app/context/` parser -> PostgreSQL; `app/worker/recovery.py`). Add `VcsDelivery` and the new `ContextPayload` columns to the data model table, and the `recovery` process to "Queue and worker".
- `docs/architecture/erd.md`: add `vcs_deliveries` (both unique constraints) and the new `context_payloads` columns (`structured_context`, `provider_metadata`, `schema_version`, `head_sha`, `base_sha`); check them against the Alembic migrations from 3.1.
- `docs/SYSTEM_DESIGN.md` §5: record the event matrix implemented by this change (GitHub `opened`/`synchronize`; GitLab `open` and `update` with a changed `oldrev`; `reopened` not handled) and the deduplication as implemented (`uq_vcs_deliveries_provider_delivery`, `uq_vcs_deliveries_revision`). In §15 mark "exact GitHub/GitLab webhook event matrix" as provisionally implemented, still open for the team. Add a note that the code uses `MergeRequest`/`ReviewJob` (`pending/running/...`), not `ReviewRun` (`NEW -> QUEUED -> ...`); this is a divergence that remains, not something to migrate here.
- `openspec/project.md` "Current implementation boundary": VCS adapters and webhook ingestion are now implemented; publishing is still future work.

Acceptance criteria:
- No document claims that webhooks or VCS adapters are future work, that the worker runs only `ping`, or that production has no recovery process.
- The SYSTEM_DESIGN changes describe what was implemented and keep the open decision visible. They do not resolve team decisions.

Verification: `uv run pre-commit run --all-files`; `openspec validate add-vcs-webhooks-context --strict` (`project.md` is not validated, but this confirms the change still validates); manual cross-check against `app/models/vcs_delivery.py`, `app/models/context_payload.py` and `alembic/versions/*`.

Test seams: None (documentation only).

Dependencies: 5.3b (avoids overlapping edits and keeps one source per fact).

Files: `docs/BACKEND_ARCHITECTURE.md`, `docs/architecture/erd.md`, `docs/SYSTEM_DESIGN.md`, `openspec/project.md`.

Scope: Small/Medium (documentation).

### Checkpoint: after 5.3

- [ ] `infra/apply.sh fmt/validate` and pre-commit pass. The CI `terraform` job is green.
- [ ] The docs cross-check is complete. The live-check status has been reported to the user.
- [ ] The user has set (or knowingly deferred) the production secrets in repository settings. Without them, production webhooks return 401 by design.

## Task 5.4: Quality and integration checks, reconciliation (no archive here)

Description: run every check and reconcile the artifacts. Record the results in `planning/phase-5/verification.md` (same role as `planning/phase-1/verification.md`). The parent does the final review, archival (`openspec archive add-vcs-webhooks-context --yes`), the commit and the PR.

Checks (actually run; report any that cannot be run, never present them as passed):
- [ ] `uv sync --locked`, then `uv run pre-commit run --all-files`
- [ ] `uv run pytest` with `TEST_DATABASE_URL` and `TEST_REDIS_URL` set to throwaway containers. No PostgreSQL/Redis-backed test is skipped (compare the skip count with a run without the variables).
- [ ] `docker compose config --quiet`, `docker compose --profile tunnel config --quiet`, `docker compose -f docker-compose.yml -f docker-compose.smoke.yml config --quiet`
- [ ] The compose stack smoke as in CI: `up -d --build --wait`, `/healthcheck`, `python -m app.worker.smoke`, `scripts/smoke_webhooks.sh`, `down -v`
- [ ] `infra/apply.sh fmt -check -diff` and `validate`
- [ ] `openspec validate add-vcs-webhooks-context --strict`
- [ ] CI on the branch: `checks`, `terraform`, `compose` green (link the run)

Reconciliation checklist:
- [ ] A spec traceability table: each scenario in `specs/webhook-ingestion`, `specs/vcs-retrieval` and `specs/context-level-one` maps to at least one passing test (file::test) and, where applicable, to a smoke case.
- [ ] Proposal "What Changes" bullets each map to delivered code or docs. Acceptance ("an authentic delivery ... produces revision-bound structured context") is covered by the CI smoke (simulated). The live provider check is recorded separately as performed (date, by whom) or not performed (reason).
- [ ] design.md matches the implementation: the bot model, token scopes, JSON-only GitHub webhooks, signed/token GitLab modes, recovery, and the tunnel. If the CI stub upstream needs to be mentioned, propose a one-sentence edit to § Verification and deployment to the parent; do not change the design silently.
- [ ] `tasks.md`: 5.1, 5.2 and 5.3 are `[x]`. 5.4 is ticked by the parent after this report. There are no unchecked boxes and no work beyond the tasks.
- [ ] Docs consistency: README, WORKFLOW, DEVOPS, BACKEND_ARCHITECTURE, erd, SYSTEM_DESIGN, `project.md` and `VCS_SETUP.md` do not contradict each other or the code. `.env.example` lists every setting `app/config.py` reads.
- [ ] Current specs that will be merged on archive (`openspec/specs/background-worker`, `review-data`): note any statement the new capabilities make stale (for example, the worker now also runs the ingestion job). Report it; do not edit specs outside the change.
- [ ] Security sweep: no secrets in the repository (`git grep -nE 'whsec_|ghp_|github_pat_|glpat-'` returns only test-only values), and no secrets, signatures, diffs or bodies in log statements added by this change.
- [ ] Phase-4 residual risks are documented (5.3b §7) and the recovery container is present in `infra/main.tf`.
- [ ] Open divergences listed for the parent: `ReviewJob` vs `ReviewRun`, the provisional event matrix, provisional limits.

Acceptance criteria: every check has passed (or is explicitly reported as not runnable, with the reason); every reconciliation item is resolved or listed as a blocker; `verification.md` written. Archival is recommended only if there are no blockers.

Verification: the commands above; the `verification.md` content.

Test seams: None (verification and reconciliation only).

Dependencies: 5.1, 5.3.

Files: `openspec/changes/add-vcs-webhooks-context/planning/phase-5/verification.md`. If reconciliation finds stale docs, fix them within this task only when the fix is a factual correction. Anything that changes requirements or design goes back to the parent.

Scope: Small (checks plus one file).

### Checkpoint: Phase 5 complete

- [ ] All checks green, verification.md written, live-check status recorded.
- [ ] Phase review by `openspec-reviewer`, then final review, archival and PR by the parent.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stub upstream drifts from adapter requests, so the CI smoke is flaky or meaningless | High | Contract test runs the real adapters against the stub in the `checks` job (5.1b). |
| Throwaway smoke secrets mistaken for or replaced by real ones | High | Literal, labelled `...-not-real` values only in the override and the CI job env; the replay script reads env vars only; security sweep in 5.4. |
| CI compose smoke races the worker (context not yet persisted) | Med | Poll delivery status with a 60 s budget; print `status`/`last_error` on timeout; assert DB state, not timing. |
| Secrets interpolated into shell (`${{ secrets.X }}` inside `run:`) or passed as SSH args | High | Follow the existing pattern: `env:` -> `jq --arg` -> stdin into a 0600 file; reviewed in 5.3a. |
| GitHub rejects secret names starting with `GITHUB_` | Med | Use the `VCS_` prefix mapping (Open Questions). |
| Production deployed without secrets or the recovery container: webhooks 401, accepted rows stuck after a Redis blip | High | 5.3a adds the wiring and `dmc268-recovery`; deploy smoke checks both; the docs state the fail-closed behaviour. |
| A webhook POST returning 401 from the deploy smoke looks like an attack in the logs | Low | One request per deploy, logged as `outcome=unauthorized` without a body; documented. |
| Non-ASCII GitLab token never matches (latin-1 vs UTF-8) | Low | Docs require ASCII secrets; behaviour unchanged (fail closed). |
| Docs drift from code (URLs, names, defaults) | Med | Item-by-item cross-check in 5.3b/5.3c and again in 5.4. |
| Live provider check unavailable to agents | Med | Reported explicitly as not performed; the simulated CI smoke is never presented as a live check. |
| macOS `infra/apply.sh` (`stat -c`) or missing Docker blocks Terraform validation locally | Low | Fallback in the script; the CI `terraform` job is authoritative; report if not run locally. |

## Open Questions

1. **Deploy wiring scope.** The approved artifacts mention configuration *documentation* (proposal bullet 7, task 5.3 "configuration"), not production infrastructure changes. Your phase-4 decision put deploy wiring of VCS tokens and webhook secrets into "the docs/config task", so this plan places it in 5.3a. Please confirm. If it is out of scope instead, 5.3a moves to a follow-up change, and the docs must state that production webhooks return 401 until it lands.
2. **Recovery container in production.** It is not part of your earlier decision, but without it the "Dispatch recovery" requirement does not hold in production. The plan adds `dmc268-recovery` in 5.3a. Confirm it, or defer it to the same follow-up as above.
3. **Repository secret/variable names.** The `GITHUB_` prefix is reserved by GitHub. Proposed names: `VCS_GITHUB_TOKEN`, `VCS_GITHUB_WEBHOOK_SECRET`, `VCS_GITLAB_TOKEN`, `VCS_GITLAB_WEBHOOK_SIGNING_TOKEN`, `VCS_GITLAB_WEBHOOK_SECRET_TOKEN` (secrets), `VCS_GITLAB_WEBHOOK_AUTH` (variable). Setting the values in repository settings requires admin access and is a user action.
4. **Live provider check.** It needs the bot account, a token, admin access to the team repository webhook settings, and a reachable URL (ngrok or production after the merge). Will you run it before archival, or should it be recorded as "not performed" and the change archived on the simulated CI evidence (the proposal allows this: "A live check requires a reachable webhook URL and provider credentials")?
5. **GitLab coverage in production.** Should GitLab secrets be wired now (as planned, empty by default), or is GitLab out of scope for the team's deployment, so only the GitHub secrets are needed?

### Resolved by the user (2026-10-12)

1–2. Confirmed: 5.3a wires VCS tokens and webhook secrets through Terraform/`ci-cd.yml` and adds the `dmc268-recovery` container; missing secrets keep the endpoint at 401 without failing the deploy.
3. The workflow currently has no VCS secrets (existing convention maps arbitrary repository secret names such as `AI_DMC268_T3` to env). Use the `VCS_*` names from question 3; the implementer lists them in DEVOPS so the user can create or remap them.
4. Live check is available: the bot account exists, its tokens are in the local `.env`, and ngrok is configured. Run the live check through the `tunnel` profile before archival (5.4); creating the provider webhook is a user action, and the result is recorded in `verification.md`.
5. Wire GitLab secrets too (empty by default), as planned.
