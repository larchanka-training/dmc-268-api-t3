# Development workflow

How a change goes from a task to production. Architectural rules — [`AGENTS.md`](../AGENTS.md); operating the environment — [`docs/DEVOPS.md`](DEVOPS.md).

## Contents

1. [Before a change](#1-before-a-change)
2. [Running locally](#2-running-locally)
3. [Checks](#3-checks)
4. [Migrations](#4-migrations)
5. [Pull requests](#5-pull-requests)
6. [Deploy and rollback](#6-deploy-and-rollback)
7. [Agents and skills](#7-agents-and-skills)

---

## 1. Before a change

1. Read the issue and its acceptance criteria. If something is ambiguous, check documentation and code first, then ask.
2. Find the relevant section of [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) and [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md).
3. Check `git status`: do not touch someone else's uncommitted changes.
4. Work on a branch from `main`. `main` is production: no direct pushes, PRs only.
5. A multi-step task gets a plan in `docs/superpowers/plans/<YYYY-MM-DD>-<slug>.md`.

## 2. Running locally

Full stack (PostgreSQL, Redis, one-shot `migrate`, API, worker):

```bash
docker compose up
```

The API is at `http://localhost:8000/healthcheck`. Check that the worker consumes jobs:

```bash
docker compose exec api python -m app.worker.smoke
```

API alone on the host (Postgres/Redis from `.env` must be reachable):

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Ollama runs externally (`OLLAMA_HOST`); Eurorouter credentials go into `.env` (`EUROROUTER_*`). Variables — the table in [`README.md`](../README.md).

## 3. Checks

Before considering a task done — the same as CI runs:

```bash
uv run pre-commit run --all-files
uv run pytest
```

pre-commit covers ruff (with auto-fix), ruff format, mypy strict, pylint, and whitespace/yaml/toml checks. If a hook modified files, review the diff and include them in the commit.

For changes in `infra/`:

```bash
infra/apply.sh fmt -check -diff
infra/apply.sh validate -no-color
```

For changes to the Dockerfile, compose, or worker — a stack smoke test, as in CI:

```bash
docker compose up -d --build --wait --wait-timeout 180
```

Do not disable linters globally; a targeted `# noqa` / `# pylint: disable=` / `# type: ignore[...]` needs a reason in a comment. If a check cannot be run (no Docker, no Postgres, etc.), say so — do not present unverified work as verified.

## 4. Migrations

1. Change the model in `app/models/`.
2. Generate a migration and review it by hand (types, indexes, `server_default`, `downgrade`):

   ```bash
   uv run alembic revision --autogenerate -m "<what changes>"
   ```

3. Apply locally: `uv run alembic upgrade head` (or recreate `migrate` in compose).
4. Update [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) and [`docs/architecture/erd.md`](architecture/erd.md) if the data model changes.

Merged migrations are never edited. A failing migration during deploy stops the rollout and the old version keeps running — but rolling the schema back takes a new migration.

## 5. Pull requests

- Description follows [`.agents/templates/pull-request.md`](../.agents/templates/pull-request.md): linked issues, changed behavior, checks with actual results, known limitations.
- Merge after green CI and human approval. Submitting for review does not close the issue.

## 6. Deploy and rollback

Deploy happens only by merging into `main`. The `.github/workflows/ci-cd.yml` pipeline: checks → image to GHCR tagged with the SHA → `infra/deploy.sh` on the VPS (migrations first, then the rest) → HTTPS and worker smoke test. Details — [`DEVOPS.md` §2](DEVOPS.md#2-how-a-deploy-works).

- Never deploy or edit containers by hand on the server; never run `terraform destroy` (the Postgres volume is protected by `prevent_destroy`).
- Rollback is `git revert <sha>` → PR → merge. Re-running an old workflow does not roll back: the deploy job skips itself if its commit is no longer the tip of `main`.
- Secrets — GitHub Encrypted Secrets ([`DEVOPS.md` §4](DEVOPS.md#4-secrets-and-configuration)).

## 7. Agents and skills

- Implementing a backend task — the [`reviewer-backend`](../.agents/skills/reviewer-backend/SKILL.md) skill with the [`implement.md`](../.agents/templates/implement.md) template; tests — [`reviewer-backend-tests`](../.agents/skills/reviewer-backend-tests/SKILL.md); diff review — [`reviewer-diff-review`](../.agents/skills/reviewer-diff-review/SKILL.md). Route — [`.agents/workflows.md`](../.agents/workflows.md).
- Give a subagent the goal, scope, files, constraints from `AGENTS.md`, and the expected checks; get back what changed, files, check results, and blockers.
- Parallel agents never edit the same files.
