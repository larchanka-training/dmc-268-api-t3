# DMC-268 API (Team 3)

## Purpose

FastAPI backend for an AI code reviewer for GitHub Pull Requests and GitLab Merge Requests. The target product analyzes code changes and publishes a review summary. OpenSpec documents the behavior present in this repository; `SYSTEM_DESIGN.md` is a draft of the broader target system and must not be mistaken for shipped behavior.

## Technology

- Python 3.12, FastAPI, Pydantic, SQLAlchemy, and PostgreSQL
- Alembic for schema migrations
- Redis and RQ for background jobs
- Ollama and Eurorouter provider adapters behind an LLM gateway
- uv for dependency management; Ruff, mypy, Pylint, pytest, and pre-commit for quality checks

## Architecture and conventions

- Keep HTTP concerns in `app/api/`; keep review orchestration separate from API handlers.
- Put provider abstractions and implementations under `app/llm/`; callers use `LLMGateway` rather than concrete providers.
- Keep SQLAlchemy entities under `app/models/` and schema changes in `alembic/`.
- Background job functions must be importable from a module and run through the Redis/RQ worker.
- Preserve strict typing and the lint/format settings in `pyproject.toml`.
- Treat provider output as untrusted and validate it into the declared Pydantic response schemas.

## Existing project documentation

- `README.md`: setup, local development, quality checks, and deployment overview.
- `BACKEND_ARCHITECTURE.md`: current layering and data model overview.
- `SYSTEM_DESIGN.md`: draft product architecture and open design questions.
- `docs/architecture/erd.md`: entity relationship diagram.
- `docs/DEVOPS.md`: deployment and operations.

## Current implementation boundary

The implemented surface includes the `/healthcheck` endpoint, persistence entities for repositories/merge requests/review jobs/context payloads/findings, structured LLM review generation with provider fallback, and an RQ worker with a `ping` smoke job. VCS adapters, webhook orchestration, and publishing reviews are described as future work in the design and are not current API contracts.
