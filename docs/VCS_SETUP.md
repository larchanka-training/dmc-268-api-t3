# VCS integration setup

How to connect the service to GitHub (and GitLab): the bot account, token permissions, webhook subscription, configuration, verification and known risks. Architecture and the queue — [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) and [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md); production secrets and deploy — [`DEVOPS.md`](DEVOPS.md); local run and the ngrok tunnel — [`WORKFLOW.md` §2](WORKFLOW.md#2-running-locally).

## Contents

1. [How it works](#1-how-it-works)
2. [GitHub bot account](#2-github-bot-account)
3. [Token permissions](#3-token-permissions)
4. [Webhook setup](#4-webhook-setup)
5. [Configuration](#5-configuration)
6. [Events and responses](#6-events-and-responses)
7. [Verification](#7-verification)
8. [Residual risks](#8-residual-risks)

---

## 1. How it works

The provider sends a signed webhook to `POST /webhooks/{github,gitlab}`. The API authenticates the raw bytes, normalizes the event, durably records it in the `vcs_deliveries` table (PostgreSQL is the source of truth) and enqueues an RQ job that carries only the delivery id. The worker then reads the pull/merge request metadata and the complete raw diff from the provider API with the bot token, checks that the head SHA did not change, and stores the structured context in `context_payloads`. A separate `recovery` process re-enqueues deliveries that were accepted but never dispatched, are due for retry, or whose worker lease expired.

The service only **reads** from the VCS in this change. It does not post comments, set statuses or change anything in the repository.

## 2. GitHub bot account

Create a dedicated machine-user GitHub account for the service and add it to the team repository as a collaborator with **Read** access. It is not a GitHub App:

- only a user account can be requested as a pull request reviewer;
- the adapter reads a static `GITHUB_TOKEN`; GitHub App installation tokens expire hourly and need a JWT exchange, which is not implemented.

Reviewer assignment is deployment context. Ingestion is triggered by the `opened` and `synchronize` actions and does not require a review request.

Never reuse a personal account or its token.

## 3. Token permissions

Give the token only what the adapters call. The adapters make GET requests only, with `follow_redirects` disabled (`app/vcs/github.py`, `app/vcs/gitlab.py`):

| Provider | Calls | Header |
|---|---|---|
| GitHub | `GET {GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{number}` twice for metadata (before and after the diff) and once with `Accept: application/vnd.github.diff` for the raw diff | `Authorization: Bearer <token>` |
| GitLab | `GET {GITLAB_API_BASE_URL}/projects/{id}/merge_requests/{iid}` twice for metadata and `.../raw_diffs` once | `PRIVATE-TOKEN: <token>` |

**GitHub**

- Organization repositories: a fine-grained token. Contents: Read, Metadata: Read, Pull requests: Read and write, Issues: Read and write. The current adapter needs only Pull requests: Read; write access to Pull requests and Issues is reserved for the summary comment of a later change (design of `add-vcs-webhooks-context`). If you want strictly the current behavior, grant read-only and widen it when publication is implemented.
- A repository in a personal account where the bot is a collaborator: fine-grained tokens cannot reach a collaborator's repository, so use a classic token with `repo` (or `public_repo` for a public repository). A classic token is broader than the adapter needs; keep the bot on Read access to the single repository.

**GitLab**

- A token with the `read_api` scope, owned by an account that can read the project's merge requests. This is derived from the endpoints above and GitLab's scope definition; it has **not** been verified against a live GitLab instance in this change, so treat it as unverified until checked. The minimal project role for private projects is also unverified.

The token must never be placed in a queue message, a log line or an LLM request.

## 4. Webhook setup

Only a repository admin can create webhooks. Use one webhook per provider and repository.

### GitHub

Repository **Settings → Webhooks → Add webhook**:

| Field | Value |
|---|---|
| Payload URL | production: `https://<SITE_ADDRESS>/api/webhooks/github` (Caddy strips `/api`); local tunnel: `https://<NGROK_DOMAIN>/webhooks/github` |
| Content type | `application/json`. The `application/x-www-form-urlencoded` form wraps the JSON in a `payload=` field and is rejected with `400` |
| Secret | the value of `GITHUB_WEBHOOK_SECRET` |
| Events | "Let me select individual events" → **Pull requests** only |

Other events are authenticated and answered `ignored`, but sending them is pointless. `ping` is handled the same way.

### GitLab

Project **Settings → Webhooks**: URL `https://<SITE_ADDRESS>/api/webhooks/gitlab` (or `https://<NGROK_DOMAIN>/webhooks/gitlab`), trigger **Merge request events** only.

Two authentication modes, selected by `GITLAB_WEBHOOK_AUTH`:

- `signed` (default): GitLab signs the request with the Standard Webhooks protocol. Put the signing token (`whsec_...`) into `GITLAB_WEBHOOK_SIGNING_TOKEN`. This is the GitLab 19.0+ mode. The timestamp must be within 300 seconds of the server clock.
- `token`: for older installations. Set `GITLAB_WEBHOOK_AUTH=token` and put the secret token configured in GitLab into `GITLAB_WEBHOOK_SECRET_TOKEN`; GitLab sends it in `X-Gitlab-Token`.

A GitLab delivery needs a `webhook-id` or `Idempotency-Key` header (GitLab 17.4 and later). Without it the request is rejected with `400`. An unknown `GITLAB_WEBHOOK_AUTH` value is a configuration error and gives `401`.

An empty secret is never accepted. If a secret is not configured, the endpoint answers `401` to everything (fail-closed). This is also the state of a production deploy that has no `VCS_*` secrets.

## 5. Configuration

Settings are read in `app/config.py`; local values go in `.env` (see `.env.example`). In production the values come from repository secrets and variables (mapping and procedure in [`DEVOPS.md` §4](DEVOPS.md#4-secrets-and-configuration)). All of them are optional for the deploy; the production secret names are not defaults of the application.

| Variable | Meaning | Default | Production source | Provisional |
|---|---|---|---|---|
| `GITHUB_TOKEN` | bot token for GitHub reads | empty | repo secret `VCS_GITHUB_TOKEN` | no |
| `GITHUB_API_BASE_URL` | GitHub API origin (must start with `http://` or `https://`) | `https://api.github.com` | code default | no |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for `X-Hub-Signature-256` | empty (401) | repo secret `VCS_GITHUB_WEBHOOK_SECRET` | no |
| `GITLAB_TOKEN` | token for GitLab reads | empty | repo secret `VCS_GITLAB_TOKEN` | no |
| `GITLAB_API_BASE_URL` | GitLab API origin | `https://gitlab.com/api/v4` | code default | no |
| `GITLAB_WEBHOOK_AUTH` | `signed` or `token` | `signed` | repo variable `VCS_GITLAB_WEBHOOK_AUTH` (empty means `signed`) | no |
| `GITLAB_WEBHOOK_SIGNING_TOKEN` | `whsec_...` signing token (signed mode) | empty (401) | repo secret `VCS_GITLAB_WEBHOOK_SIGNING_TOKEN` | no |
| `GITLAB_WEBHOOK_SECRET_TOKEN` | `X-Gitlab-Token` secret (token mode) | empty (401) | repo secret `VCS_GITLAB_WEBHOOK_SECRET_TOKEN` | no |
| `VCS_TIMEOUT_SECONDS` | timeout of each VCS API request | `30` | code default | yes |
| `VCS_MAX_DIFF_TOKENS` | diff size limit; an over-limit diff is a failure, never truncated | `32000` | code default | yes |
| `RECOVERY_INTERVAL_SECONDS` | how often the recovery process scans | `30` | code default | yes |
| `RECOVERY_STALE_AFTER_SECONDS` | a dispatched but unclaimed delivery older than this is re-enqueued | `300` | code default | yes |
| `REDIS_URL` | queue (shared with the existing worker) | `redis://redis:6379/0` | code default | no |
| `NGROK_DOMAIN`, `NGROK_AUTHTOKEN` | local tunnel only | empty | not deployed | no |

Constants in the code that are also **provisional** (SYSTEM_DESIGN §14–15 still has these decisions open): the 300 s freshness window for signed GitLab requests, the 25 MiB webhook body limit, the delivery attempt limit (5) with a 30 s exponential backoff capped at 1 h, and the 5 min processing lease. They are not configurable yet.

GitHub does not allow secret or variable names that start with `GITHUB_`, which is why the repository names carry the `VCS_` prefix.

## 6. Events and responses

Handled events (`app/webhooks/github.py`, `app/webhooks/gitlab.py`; the event matrix is provisional and open for the team):

| Provider | Handled | Not handled (authenticated, answered `ignored`) |
|---|---|---|
| GitHub | `X-GitHub-Event: pull_request` with action `opened` or `synchronize` | other events (including `ping`), other actions (`reopened`, `closed`, ...) |
| GitLab | `X-Gitlab-Event: Merge Request Hook` with action `open`, or `update` whose `oldrev` is present and differs from the head commit | other events and actions, `update` without a source commit change |

Responses of `POST /webhooks/{github,gitlab}` (`app/api/webhooks.py`). Successful responses carry JSON `{"status": "..."}`:

| Code | Body status / meaning |
|---|---|
| `200` | `accepted`: a new delivery was recorded and a job enqueued |
| `200` | `duplicate`: the delivery id was seen before, or the same revision was already recorded; nothing new is enqueued |
| `200` | `ignored`: authentic but unsupported event or action; nothing is stored |
| `400` | invalid JSON (including a form-encoded GitHub body) or a payload that fails field validation, including a missing or invalid delivery id header (`X-GitHub-Delivery`; `webhook-id` or `Idempotency-Key` for GitLab) |
| `401` | authentication failed or the secret is not configured (the same response, so configuration state is not leaked) |
| `413` | body larger than 25 MiB |
| `503` | the database is unavailable; the provider should redeliver |

A queue (Redis) outage does not change the response: the delivery is already committed, the API still answers `accepted`, and the recovery process dispatches it later.

## 7. Verification

### Simulated (no provider account)

These prove the service behavior with locally signed deliveries. They do **not** prove that GitHub or GitLab accepts the configuration, that the bot token works against the real API, or that your webhook settings are correct.

- `scripts/replay_webhook.py` sends a payload file with locally built headers. The secret is read from an environment variable (default: the backend's variable name, or `--secret-env NAME`) and never printed. Flags: `{github,gitlab}`, `--url`, `--payload`, `--event`, `--delivery-id`, `--gitlab-mode {signed,token}`, `--secret-env`, `--unsigned`, `--timestamp-offset`, `--expect-status`, `--expect`. Run `python3 scripts/replay_webhook.py --help`. Example fixtures are in `tests/webhooks/fixtures/`.
- `scripts/smoke_webhooks.sh` runs the full path against the compose stack with `docker-compose.smoke.yml` (throwaway test secrets, a fake VCS upstream): signed GitHub and GitLab deliveries become `succeeded` with stored context, redelivery gives `duplicate`, bad signatures give `401`, `ping` gives `ignored`. The same script runs in the CI `compose` job:

  ```bash
  COMPOSE_FILE=docker-compose.yml:docker-compose.smoke.yml docker compose up -d --build --wait
  scripts/smoke_webhooks.sh
  docker compose down -v
  ```

### Live (real provider)

Needs the bot account, its token in `.env` (`GITHUB_TOKEN`), a webhook secret in `.env` (`GITHUB_WEBHOOK_SECRET`), a reachable URL and admin access to the repository settings. The steps are an action of the person with these accounts:

1. Start the stack with the tunnel: set `NGROK_DOMAIN` and `NGROK_AUTHTOKEN` in `.env`, then `docker compose --profile tunnel up` (or host ngrok, see [`WORKFLOW.md` §2](WORKFLOW.md#real-github-webhook-deliveries-ngrok)). For production use the production URL instead.
2. Create the webhook as described in [section 4](#4-webhook-setup) with `https://<NGROK_DOMAIN>/webhooks/github`.
3. In the repository open a pull request (or push a commit to an open one).
4. In GitHub **Settings → Webhooks → Recent Deliveries** the delivery should show `200` and `{"status":"accepted"}`. The ngrok inspector (`http://127.0.0.1:4040`) shows the same request. **Redeliver** should produce `duplicate`.
5. In the database check the result:

   ```sql
   select provider, delivery_id, lower(status) as status, attempts, last_error from vcs_deliveries order by created_at desc limit 5;
   select head_sha, base_sha, length(diff_text) > 0 as has_diff, schema_version from context_payloads order by created_at desc limit 5;
   ```

   `status` must reach `succeeded` (the smoke script compares `lower(status)`); `last_error` must be empty. Do not copy diffs or tokens into issues or chat.

Troubleshooting: `401` in Recent Deliveries is a secret mismatch or an empty `GITHUB_WEBHOOK_SECRET`; `400` is usually the form-encoded content type; a `502` or an ngrok error page means the API is not running on port 8000; status `pending` for a long time means the `recovery` or `worker` container is not running.

Result of the live check is recorded separately from the simulated checks (who and when, in `planning/phase-5/verification.md` of the change).

## 8. Residual risks

- **The recovery process must run in every environment.** The API commits the delivery before it touches the queue. If the response is lost after the commit (for example a `503` on the way back, or a provider timeout), the provider redelivers and gets `duplicate`, and the stored row is dispatched only by the recovery process. Without `python -m app.worker.recovery` (compose service `recovery`, production container `dmc268-recovery`) such deliveries stay `pending` forever.
- **GitLab token mode needs ASCII-only secrets.** HTTP header values are decoded as latin-1, while the configured secret is compared as UTF-8; a non-ASCII secret never matches. Use ASCII-only secrets.
- **`repository_url` is stored without scheme validation.** It is taken from the payload (`repository.html_url`, `project.web_url`, up to 2048 characters), is for display only and is never fetched; the adapters use only the configured API origins. Do not render it as a raw link in a UI or a comment without validating the scheme.
- **A GitHub repository rename changes `owner/name`**, which is the repository key, so the same pull request is seen as a different one and the revision-level deduplication does not carry across the rename.
- **Missing production secrets are not a deploy failure.** The deploy succeeds and the webhook endpoints answer `401` until `VCS_*` secrets are created. The deploy smoke checks exactly this `401` and that `dmc268-recovery` is running.
- **Live provider behavior is not exercised by CI.** The GitLab minimal token scope and the real GitLab `oldrev` and header behavior are verified only against fixtures.
