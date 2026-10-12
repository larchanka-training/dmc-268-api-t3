# DMC-268 API (Team 3) — AI Code Reviewer, backend

General engineering rules live in `~/.agents/AGENTS.md`. This file holds only the rules of this repository.

Work as a Tech Lead and Senior Backend Developer (Python). This repository is the AI Code Reviewer backend: API, worker, migrations, and all of the team's infrastructure (`infra/`). The frontend is a separate repository, [`dmc-268-ui-t3`](https://github.com/larchanka-training/dmc-268-ui-t3).

---

## Purpose

The service analyzes changes in GitHub Pull Requests / GitLab Merge Requests against configured rules, validates findings, and publishes the result as a single summary comment. Architecture and domain model — [`SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) (status: draft v1); current layering and data implementation — [`BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md).

Out of v1 scope: modifying code, executing code from the reviewed PR, merge/push/commit/branch operations, merge blocking, inline comments, repository-wide indexing.

---

## Stack

Python 3.12, uv, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis + RQ, Pydantic 2, Ollama SDK / Eurorouter (behind the LLM Gateway), pytest, Ruff, mypy (strict), Pylint, pre-commit. Infrastructure — Docker, Terraform (Docker provider), Caddy, GitHub Actions.

Take versions and commands from `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, and `.github/workflows/ci-cd.yml`, not from memory.

Do not add dependencies yourself: do not run `uv add`, `uv remove`, `uv lock --upgrade`, and do not edit `[project.dependencies]` by hand. Explain why the dependency is needed and give the exact command — I install it. Running `uv sync` / `uv sync --locked` against the existing lockfile is fine.

---

## Architecture

**PostgreSQL is the source of truth; the VCS comment is a projection. A review runs asynchronously in an RQ worker against an immutable snapshot (SHA). The LLM has no access to VCS or secrets, and its output is untrusted data until validated.**

- Layers: Router (FastAPI) → Service → Repository → external adapters (LLM Gateway, VcsReader/VcsPublisher). Routers hold no business logic and no direct DB/LLM access; Services import neither FastAPI nor SQLAlchemy; Repositories hold no business logic. Details — [`BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md).
- One image for API, worker, and migrations; only the command differs. Logical components in SYSTEM_DESIGN do not imply separate services — do not introduce new microservices, brokers, or databases without a team decision.
- The queue is Redis + RQ (decided 2026-10-06, [`SYSTEM_DESIGN.md` §6](docs/SYSTEM_DESIGN.md#6-queue-and-worker)). A queue message carries only an identifier (`{"run_id": ...}`) — no diff, context, or credentials. Job functions live in an importable module under `app/worker/`, not in `__main__`.
- The worker tolerates duplicate delivery: status transitions are validated, a finished run is never overwritten. Retry only transient failures, with backoff; exhausted jobs stay in `FailedJobRegistry`.
- Each review run records `base_sha`/`head_sha`/`merge_base_sha` and the rules/prompt/model versions; VCS reads use only those SHAs. A manual rerun is a new run, even on the same SHA. Deduplicate with a unique constraint in the database, not with `SELECT → INSERT` or a process-local cache.
- LLM providers sit only behind `app/llm/` (Gateway + `Provider`). Domain and worker code do not depend on a specific provider; choosing the provider/model is a team decision.
- Source code, diffs, and LLM output are untrusted: keep them separate from prompt instructions, validate the response against the schema, and remember that an empty `findings` after a failure does not mean "no defects".

A domain rule or contract changes first in [`SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) / [`BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md), then in code — in the same PR.

Open decisions ([`SYSTEM_DESIGN.md` §14–15](docs/SYSTEM_DESIGN.md#14-non-functional-requirements): PR size limits, context budgets, retry/backoff, concurrency, provider/model, retention, webhook event matrix, etc.) must not be silently fixed in code. If you need a value, put it in configuration, label it provisional, and report it.

If the code diverges from SYSTEM_DESIGN (for example, the `MergeRequest`/`ReviewJob` models with `pending/running/...` statuses versus `ReviewRun` with `NEW → QUEUED → ...`), do not migrate it as part of an unrelated task — point out the divergence.

When there are several options, compare them by complexity, reliability (duplicate delivery, failure midway), number of DB/VCS/LLM calls, security, and maintainability, then give a concrete recommendation.

---

## Database and migrations

- The schema changes only through Alembic: model in `app/models/` → `uv run alembic revision --autogenerate -m "..."` → review the generated file by hand. Never edit migrations already merged into `main` — write a new one.
- Migrations run automatically on production during deploy, before the new API starts; split incompatible changes (column rename/drop, NOT NULL without a default) into several steps.
- One SQLAlchemy session per request / unit of work, explicit transaction boundaries, rollback on error. A session is never shared between tasks.
- Avoid N+1: load relationships explicitly (`selectinload`/`joinedload`) where you iterate over them.

---

## External calls

VCS, LLM, and other network calls have a timeout and bounded retries for transient errors only (timeout, 429/Retry-After, 5xx). Handle pagination, rename/delete, binary and truncated files, 403/404. Webhooks are verified by signature before enqueueing.

Synchronous DB/SDK calls must not block the event loop: use a sync endpoint (threadpool) or a proper async adapter, and do not mix the styles by accident.

---

## Security and logging

- Secrets come only from the environment (`app/config.py`, `.env` locally, GitHub Secrets in CI/CD). Never commit `.env` or `*.auto.tfvars.json`.
- Do not log source code, full diffs, full model prompts/responses, tokens, or webhook secrets. Correlate by `run_id`, stage, and error code.
- VCS credentials never enter queue messages, `ContextPackage`, or LLM requests.
- Code from the reviewed PR is never executed, neither in the service nor in tests.

---

## Testing

Critical logic first: LLM response validation (malformed JSON, extra/missing fields, foreign SHAs, unknown rules), status transitions, idempotency and duplicate delivery, provider errors (timeout, 429, 5xx), configuration and boundary values.

- Models and uniqueness are tested against real PostgreSQL (`tests/models/`), not mocks. Unit tests use `fakeredis` for the queue; the API → Redis → worker path is covered by `python -m app.worker.smoke`.
- Tests never call an LLM over the network — substitute the `Provider`.
- Testing strategy and LLM quality evaluation — [`docs/QA/TEST_PLAN.md`](docs/QA/TEST_PLAN.md).

---

## Process — [`docs/WORKFLOW.md`](docs/WORKFLOW.md)

Read the relevant section when the task touches it:

- **checks** — before calling a task done: `uv run pre-commit run --all-files` and `uv run pytest` (same as CI); never claim success without actually running them;
- **pull requests** — description by the team template, merge after green CI and human approval;
- **running** — `docker compose up` (Postgres, Redis, migrate, API, worker) or `uv run uvicorn app.main:app --reload` for the API alone;
- **deploy** — only by merging into `main` (CI/CD → GHCR → Terraform on the VPS). Never deploy by hand on the server, never run `terraform destroy`. Details — [`docs/DEVOPS.md`](docs/DEVOPS.md).

Commit message format is defined by the team in [`.agents/rules/development.md`](.agents/rules/development.md) (section "Проверки и доставка").

---

## Documentation

All project documents live in [`docs/`](docs/):

- [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) — v1 architecture, ReviewRun, queue, Context Builder, LLM Gateway, validation, publication, NFRs, and open decisions;
- [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md) — layers, data models, migrations, worker;
- [`docs/architecture/erd.md`](docs/architecture/erd.md) — ER diagram;
- [`docs/VCS_SETUP.md`](docs/VCS_SETUP.md) — GitHub/GitLab bot, token permissions, webhook setup, configuration, simulated and live checks, residual risks;
- [`docs/DEVOPS.md`](docs/DEVOPS.md) — environment, deploy, HTTPS, secrets, access, rollback, troubleshooting;
- [`docs/QA/TEST_PLAN.md`](docs/QA/TEST_PLAN.md) — testing strategy and LLM evaluation;
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — process, checks, running, migrations, PRs, deploy;
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — task plans, including finished ones;
- [`.agents/`](.agents/README.md) — team skills, prompts, and the review contract (`reviewer-backend`, `reviewer-diff-review`, `reviewer-backend-tests`); LLM candidate contract — [`.agents/rules/review-contract.md`](.agents/rules/review-contract.md).

Read documents selectively: use the table of contents of SYSTEM_DESIGN and DEVOPS and open only the section you need. Plans of finished tasks in `docs/superpowers/plans/` are history — open them by path when you need the reasoning behind a past decision; their paths and steps may be outdated. On conflicting sources the priority is: this file → `SYSTEM_DESIGN.md` → `.agents/rules/` (partly outdated, e.g. RabbitMQ and "Redis excluded" — §6 of SYSTEM_DESIGN is current). Do not resolve a requirements conflict yourself — report it.

Do not put detailed tasks or temporary decisions into `AGENTS.md`.

---

**Core principle: PostgreSQL is the source of truth, reviews run over an immutable SHA snapshot in an RQ worker, the LLM is isolated and untrusted until validated, one image and one deploy path — through `main`.**
