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

Starts PostgreSQL, Redis, a one-shot `migrate` container (`alembic upgrade head`), the API and the worker. The API is at http://localhost:8000/healthcheck once the stack is up. Check that the worker consumes jobs:

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

## Deployment

Every push to `main` runs `.github/workflows/ci-cd.yml`: lint/types/tests, Terraform validation and a `docker compose` smoke test; only if all are green it builds `ghcr.io/larchanka-training/dmc-268-api-t3:<sha>` and deploys it to the team VPS at **http://77.237.236.234/** (API under `/api/`, e.g. `/api/healthcheck`, `/api/docs`). The frontend repo `dmc-268-ui-t3` deploys its static image the same way.

```
Internet :80 ──► dmc268-caddy ──/api/*──► dmc268-api ──► dmc268-postgres
                               └─ /*  ──► dmc268-web    dmc268-worker ──► dmc268-redis
```

- **Infrastructure as code:** `infra/` (Terraform, Docker provider). The workflow copies it to `/opt/dmc268/infra` on the VPS and runs `infra/deploy.sh` there (Terraform in a container, under `flock /opt/dmc268/deploy.lock`). Terraform state lives in that directory on the server. Never run `destroy` there — it deletes the database volume.
- **Migrations** run in the one-shot `dmc268-migrate` container on every deploy, applied *before* anything else is replaced; if they fail, the deploy fails and the previous API and worker keep running.
- **Secrets** are GitHub Encrypted Secrets, written at deploy time into `/opt/dmc268/infra/api.auto.tfvars.json` (mode 0600) and passed to containers as environment variables:

| Name | Kind | Used for |
|---|---|---|
| `VPS_DMC268_IP_T3` | org variable | server address |
| `VPS_DMC268_U` / `VPS_DMC268_P` | org secrets | root password login, bootstrap workflow only |
| `AI_DMC268_T3` / `AI_DMC268_URL` | org secret / variable | `EUROROUTER_API_KEYS` / `EUROROUTER_BASE_URL` |
| `DEPLOY_SSH_KEY` | repo secret (this repo and the UI repo) | SSH key of the `deploy` user |
| `POSTGRES_PASSWORD` | repo secret | database password — do not rotate without `ALTER USER` in the DB |

- **First-time server setup:** `.github/workflows/bootstrap.yml` (idempotent; run from the Actions tab) installs Docker, creates the key-only `deploy` user and `/opt/dmc268`.
- **Rollback:** open the Actions run of the last good commit and use *Re-run jobs → deploy*; its image is still in GHCR.
- **Debugging:** `ssh -i <deploy key> deploy@77.237.236.234`, then `docker ps`, `docker logs dmc268-api`, `docker logs dmc268-worker`.

## Health check

```
GET http://localhost:8000/healthcheck
```

Returns `200 OK` with `{"status": "ok"}`.
