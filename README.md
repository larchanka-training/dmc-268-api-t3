# DMC-268 API (Team 3)

FastAPI backend for DMC-268 Team 3.

## Stack

- Python 3.12
- [uv](https://docs.astral.sh/uv/) — dependency and environment management
- FastAPI — web framework
- SQLAlchemy — database access
- PostgreSQL — relational database
- [Ollama SDK](https://github.com/ollama/ollama-python) — AI integration (configured, not yet used)
- Ruff — linting and formatting
- Pylint — static analysis

## Install dependencies

```bash
uv sync
```

## Run locally

```bash
uv run uvicorn app.main:app --reload
```

Then visit http://localhost:8000/healthcheck.

## Run the full local environment (API + PostgreSQL)

```bash
docker compose up
```

The API is available at http://localhost:8000/healthcheck once the stack is up.
Ollama is expected to run externally; point `OLLAMA_HOST` at it (see `.env.example`).

## Code quality

```bash
uv run ruff check .
uv run ruff format --check .
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
| `OLLAMA_HOST` | Ollama server URL | `http://host.docker.internal:11434` |

## Health check

```
GET http://localhost:8000/healthcheck
```

Returns `200 OK` with `{"status": "ok"}`.
