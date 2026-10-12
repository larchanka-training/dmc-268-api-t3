# DMC-268 API (Team 3)

FastAPI backend for DMC-268 Team 3.

## Stack

- Python 3.12
- [uv](https://docs.astral.sh/uv/) — dependency and environment management
- FastAPI — web framework
- SQLAlchemy — database access
- PostgreSQL — relational database
- Redis + [RQ](https://python-rq.org/) — background job queue and worker
- [Ollama SDK](https://github.com/ollama/ollama-python) — AI integration (configured, not yet used)
- Ruff — linting and formatting
- Pylint — static analysis
- mypy (strict, pydantic plugin) — type checking
- pre-commit — git hooks that auto-fix (ruff, ruff format, whitespace) and run mypy + pylint before every commit

## Install dependencies

```bash
uv sync
```

## Run locally

```bash
uv run uvicorn app.main:app --reload
```

Then visit http://localhost:8000/healthcheck.

## Run the full local environment

```bash
docker compose up
```

Starts PostgreSQL, Redis, a one-shot `migrate` container (`alembic upgrade head`), the API, the worker and the `recovery` process (re-dispatches stored webhook deliveries). The API is at http://localhost:8000/healthcheck once the stack is up. Check that the worker consumes jobs:

```bash
docker compose exec api python -m app.worker.smoke   # exit code 0 = OK
```

Ollama is expected to run externally; point `OLLAMA_HOST` at it (see `.env.example`). Put Eurorouter credentials in `.env` (`EUROROUTER_*`) — it is read by the api and worker containers if present.

## Code quality

Install the git hooks once per clone:

```bash
uv run pre-commit install
```

After that every `git commit` auto-fixes formatting/lint issues (re-stage and commit again if files changed) and blocks the commit on type or lint errors. Run everything by hand:

```bash
uv run pre-commit run --all-files   # what CI runs
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pylint app
```

## Tests

```bash
uv run pytest
```

PostgreSQL tests (`tests/db`, migrations and constraints) run only when `TEST_DATABASE_URL` points at a throwaway database, e.g. `postgresql+psycopg://postgres:test@localhost:55432/postgres`; they wipe its `public` schema and are skipped when it is unset.

## Environment variables

See `.env.example`. Copy it to `.env` and adjust as needed:

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_DB` | Database name | `app` |
| `POSTGRES_USER` | Database user | `app` |
| `POSTGRES_PASSWORD` | Database password | `app` |
| `POSTGRES_HOST` | Database host | `postgres` |
| `POSTGRES_PORT` | Database port | `5432` |
| `REDIS_URL` | Redis connection URL (queue) | `redis://redis:6379/0` |
| `OLLAMA_HOST` | Ollama server URL | `http://host.docker.internal:11434` |
| `EUROROUTER_BASE_URL` | Eurorouter API base URL | empty |
| `EUROROUTER_API_KEYS` | Comma-separated Eurorouter API keys | empty |
| `EUROROUTER_MODEL` | Eurorouter model name | `gpt-4o-mini` |
| `GITHUB_TOKEN` | Bot token for GitHub reads | empty |
| `GITHUB_API_BASE_URL` | GitHub API origin | `https://api.github.com` |
| `GITHUB_WEBHOOK_SECRET` | Secret for `X-Hub-Signature-256`; empty rejects every GitHub webhook | empty |
| `GITLAB_TOKEN` | Token for GitLab reads | empty |
| `GITLAB_API_BASE_URL` | GitLab API origin | `https://gitlab.com/api/v4` |
| `GITLAB_WEBHOOK_AUTH` | `signed` or `token` | `signed` |
| `GITLAB_WEBHOOK_SIGNING_TOKEN` | GitLab signing token (`whsec_...`) | empty |
| `GITLAB_WEBHOOK_SECRET_TOKEN` | GitLab secret token (token mode) | empty |
| `VCS_TIMEOUT_SECONDS` | Timeout of each VCS API request (provisional) | `30` |
| `VCS_MAX_DIFF_TOKENS` | Diff size limit (provisional) | `32000` |
| `RECOVERY_INTERVAL_SECONDS` | Recovery scan interval (provisional) | `30` |
| `RECOVERY_STALE_AFTER_SECONDS` | Re-enqueue age of an unclaimed delivery (provisional) | `300` |
| `NGROK_DOMAIN` | Local only: personal ngrok static domain for real webhook deliveries | empty |
| `NGROK_AUTHTOKEN` | Local only: ngrok authtoken for the `tunnel` compose profile | empty |

Setting up the bot account, tokens, webhooks and the live check — [`docs/VCS_SETUP.md`](docs/VCS_SETUP.md).

## Deployment

Live: **https://77-237-236-234.sslip.io/** (API health: `/api/healthcheck`, API docs: `/api/docs`; plain `http://` redirects to HTTPS).

Every push to `main` runs `.github/workflows/ci-cd.yml`: lint/types/tests, Terraform validation and a `docker compose` smoke test; only if all are green it builds an image, pushes it to GHCR and deploys it to the team VPS (Terraform in `infra/`, secrets from GitHub Encrypted Secrets). The frontend repo [`dmc-268-ui-t3`](https://github.com/larchanka-training/dmc-268-ui-t3) deploys the same way.

**Everything about operating this — architecture, how a deploy works, HTTPS and switching to a real domain, secrets, server access and handover between DevOps, rollback, troubleshooting — is in [`docs/DEVOPS.md`](docs/DEVOPS.md).**

## Health check

```
GET http://localhost:8000/healthcheck
```

Returns `200 OK` with `{"status": "ok"}`.
