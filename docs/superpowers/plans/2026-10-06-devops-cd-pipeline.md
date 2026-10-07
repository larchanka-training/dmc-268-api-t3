# Sprint 2 DevOps: Server Environment, Redis Worker, Tooling Gaps and CD Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every merge to `main` in `dmc-268-api-t3` (backend + worker) and `dmc-268-ui-t3` (frontend static) builds Docker images and deploys them to the team VPS at `http://77.237.236.234/`, with PostgreSQL + Redis managed by Terraform and secrets coming from GitHub Encrypted Secrets — and the sprint-1 tooling gaps (mypy, pre-commit, Redis, frontend lint/format/hooks) are closed on the way.

**Architecture:** One backend image runs three roles (API, RQ worker, one-shot Alembic migration). Terraform (kreuzwerker/docker provider) runs *on the VPS* inside a `hashicorp/terraform` container against the local Docker socket, so its state lives on the server next to the containers it describes — no remote-state service, no Docker-over-SSH. A Caddy container is the only thing with a public port: `/api/*` → API, everything else → the frontend's nginx image. GitHub Actions builds images to GHCR, then over SSH (as a key-only `deploy` user created once by a bootstrap workflow) writes secrets into a `0600` tfvars file, pulls the image and runs `infra/deploy.sh` (under `flock`: migrations-only apply first, then the full apply).

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2, Alembic, Redis 8, RQ 2.12, ruff, mypy (strict + pydantic plugin), pylint, pre-commit 4.6, Docker / Compose, Terraform 1.16.5 + kreuzwerker/docker ~> 4.6, Caddy 2.10, GitHub Actions + GHCR. Frontend: React 18, Vite 5, TypeScript 5.9, pnpm 12.9.1, ESLint 10 (flat config, typescript-eslint strictTypeChecked), Prettier 3.9, Stylelint 17, Husky 9 + lint-staged 17, nginx-unprivileged 1.30.

**Spec:** Sprint task texts provided in conversation on 2026-10-06 (no repo file):
- Sprint 2 — "Развертывание окружения на сервере и настройка сквозного CD-пайплайна". DoD: окружение доступно по IP; бэкенд, воркер и фронтенд автоматически деплоятся через CI/CD при мерже.
- Sprint 1 backend task 1 gaps — mypy strict (`mypy .` clean), pre-commit hooks, Redis in `docker-compose.yml`.
- Sprint 1 frontend task 1 — strict TS, ESLint, Prettier, Stylelint, Husky, pnpm; DoD: `pnpm lint`, `pnpm check-types`, `pnpm build` pass; Husky blocks commits with lint errors.
- User decisions: Redis (not RabbitMQ) for the queue; build a minimal worker now (real review jobs stay in issue #13); `main` is prod, no staging; no domain (plain HTTP on IP); secrets in GitHub Encrypted Secrets; **frontend sprint-1 task 2 (architecture doc, Zustand/TanStack/UI kit, diff components) is out of scope** — doing it partially would not meet its DoD (it requires real components), and it is not needed by the pipeline.

Prior art studied (read-only): teams 1, 4, 6 (`dmc-268-{api,ui}-t{1,4,6}`). Patterns adopted: local `uv run` pre-commit hooks (t1/t6), one image + many commands (all), one-shot migrate with exit-code postcondition (t1), sshpass-once bootstrap → key-only `deploy` user (t4), pinned `known_hosts` file (t1), throwaway `DOCKER_CONFIG` for GHCR pulls (t6), Caddy single entry with `handle_path /api/*` + `--root-path /api` (t4), disabling LLMNR (t1/t4 both hit port 5355). Avoided: self-hosted S3 state backend (t1), decorative Terraform that is never applied (t6), hand-rolled AMQP topologies (t1/t6), deploying even when tests are red (t6).

## Global Constraints

- Python `>=3.12,<3.13`; dependencies only via `uv add` (never hand-edit `uv.lock`).
- Queue: Redis 8 (`redis:8-alpine`) + `rq>=2.12`, `redis>=8.1`. No RabbitMQ, Celery, arq.
- Single environment: `main` = production. Deploy jobs run only on `push` to `main` or manual `workflow_dispatch`, and only after every check job is green (`needs:`).
- Secrets live only in GitHub Encrypted Secrets. Never commit them, never `echo` them, never pass them as command-line arguments on the server. On the server they exist only in `/opt/dmc268/infra/api.auto.tfvars.json` (mode `0600`) and Terraform state in the same `0700` directory.
- Existing org secrets/vars (do not rename): secrets `VPS_DMC268_U`, `VPS_DMC268_P` (bootstrap only), `AI_DMC268_T3`; variables `VPS_DMC268_IP_T3` (= `77.237.236.234`), `AI_DMC268_URL` (= `https://api.eurouter.ai/api/v1`).
- New repo secrets: `DEPLOY_SSH_KEY` (both repos), `POSTGRES_PASSWORD` (api repo). GitHub forbids secret names starting with `GITHUB_`.
- Server layout: user `deploy` (key-only, in `docker` group), directory `/opt/dmc268` (`0750`), Terraform dir `/opt/dmc268/infra` (`0700`), lock file `/opt/dmc268/deploy.lock`.
- Only Caddy publishes a host port (80). Postgres, Redis, API, worker, web have **no** published ports on the server.
- Container names: `dmc268-{postgres,redis,migrate,api,worker,web,caddy}`; network `dmc268`; volumes `dmc268_{postgres,redis,caddy}_data`.
- Images: `ghcr.io/larchanka-training/dmc-268-api-t3:<git sha>`, `ghcr.io/larchanka-training/dmc-268-ui-t3:<git sha>`.
- Terraform: `hashicorp/terraform:1.16.5` image via `infra/apply.sh` everywhere (laptop, CI, server); provider `kreuzwerker/docker ~> 4.6`; `.terraform.lock.hcl` committed with `linux_amd64` + `linux_arm64` hashes. **Never run `destroy` on the server** (it deletes the Postgres volume).
- Frontend: `packageManager: pnpm@12.9.1`, `.nvmrc` = `24`, `engines.node` = `>=22.22.1`. Do not upgrade React 18 / Vite 5 / TypeScript 5 in this work (typescript-eslint 8.71 supports TS `<6.1`).
- GitHub Actions versions: `actions/checkout@v7`, `astral-sh/setup-uv@v10`, `docker/setup-buildx-action@v4`, `docker/login-action@v4`, `docker/build-push-action@v7`, `actions/setup-node@v7`, `pnpm/action-setup@v6`. The org allows all actions.
- Outward-facing steps (`git push`, `gh pr create`, `gh secret set`, running the bootstrap workflow, merging) require the user's go-ahead at execution time.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (add it as a second `-m` to each `git commit` below).
- Code style: match surrounding code — existing tests have **no** return annotations (`def test_x():`), comments are sparse and explain *why*.

## Review Focus

1. **Postgres password with URL-special characters** (`@ : / %`, e.g. from `openssl rand -base64`) — today `database_url` interpolates it raw, and Alembic's ConfigParser chokes on `%` (`ValueError: invalid interpolation syntax`, reproduced). Expected: app and migrations connect with any password. Pinned by Task 3 unit test + Task 5 and Task 7 runs with password `p@ss%w0rd`.
2. **A failing migration on deploy** — expected: deploy goes red, the old API/worker keep running, new code never starts against the old schema. Reproduced during planning: a *single* `terraform apply` stops the old API before it evaluates the migrate postcondition (site down, 502). Hence `infra/deploy.sh` applies `-target=docker_container.migrate` first. Pinned by Task 7 Step 7 (deploy an image where `alembic` fails, verify API container unchanged and still 200).
3. **Re-running a deploy for the same commit** (or a frontend-only deploy) — expected: idempotent, Postgres data survives. Pinned by Task 7 Step 6 (row survives re-apply).
4. **Frontend deploy runs before the backend has ever deployed** (or both deploy at once) — expected: UI deploy fails fast with a clear message; concurrent applies serialize instead of corrupting state. Pinned by Task 13 Step 5 guard check and the `flock` around every apply (Task 10, Task 13).
5. **Worker container running but not consuming jobs** (bad `REDIS_URL`, crash loop) — expected: deploy goes red. Pinned by `python -m app.worker.smoke` in CI (Task 8) and in the deploy smoke step (Task 10); unit-tested in Task 4 (`run_smoke` returns `False` when no worker picks up the job).

---

## File Structure

**Backend repo `dmc-268-api-t3`** (branch `feature/devops-cd`):

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml`, `uv.lock` | modify | mypy config; deps `rq`, `redis`; dev deps `mypy`, `pre-commit`, `fakeredis` |
| `.pre-commit-config.yaml` | create | auto-fixing + static-check hooks (same commands CI runs) |
| `tests/llm/test_schemas.py`, `tests/llm/test_eurorouter_provider.py` | modify | 4 typing fixes so `mypy` is clean |
| `docs/QA/TEST_PLAN.md`, `alembic/README` | modify | end-of-file fixes from pre-commit; TEST_PLAN RabbitMQ→Redis |
| `app/config.py` | modify | URL-quote DB credentials; `redis_url` setting |
| `alembic/env.py` | modify | escape `%` for ConfigParser |
| `tests/test_config.py` | create | quoting + `REDIS_URL` tests |
| `app/queue.py` | create | `get_redis()`, `get_queue()`, `QUEUE_NAME` |
| `app/worker/__init__.py`, `__main__.py`, `tasks.py`, `smoke.py` | create | RQ worker entrypoint, placeholder `ping` job, end-to-end smoke check |
| `tests/worker/test_worker.py` | create | worker/queue/smoke tests on `fakeredis` |
| `Dockerfile`, `.dockerignore` | modify | ship Alembic in the image; keep `infra/` out of build context |
| `docker-compose.yml`, `.env.example` | modify | `redis`, `worker`, `migrate` services |
| `SYSTEM_DESIGN.md`, `BACKEND_ARCHITECTURE.md` | modify | record Redis/RQ decision + worker layout |
| `infra/versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `Caddyfile`, `apply.sh`, `deploy.sh`, `.gitignore`, `.terraform.lock.hcl` | create | server environment as code; `deploy.sh` = locked, migrations-first apply |
| `infra/bootstrap/bootstrap.sh`, `infra/bootstrap/deploy_key.pub` | create | one-time idempotent server prep |
| `.github/ssh/known_hosts` | create | pinned VPS host keys |
| `.github/workflows/ci-cd.yml` | create | checks → image → deploy |
| `.github/workflows/bootstrap.yml` | create | runs `bootstrap.sh` as root via password once |
| `README.md` | modify | tooling, local stack, deployment + secrets docs |

**Frontend repo `dmc-268-ui-t3`** (branches `feature/frontend-tooling`, then `feature/frontend-cd`):

| File | Action | Responsibility |
|---|---|---|
| `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `.nvmrc` | create/modify | pnpm 12, scripts, devDeps, lint-staged config |
| `eslint.config.js`, `.prettierrc.json`, `.prettierignore`, `stylelint.config.js` | create | lint/format config |
| `.husky/pre-commit`, `.husky/pre-push` | create | block bad commits/pushes |
| `tsconfig.json`, `src/App.tsx`, `src/main.tsx` | modify | lint fixes, include `vite.config.ts` |
| `README.md` | modify | commands |
| `Dockerfile`, `.dockerignore`, `docker/nginx.conf` | create | static image (nginx-unprivileged on 8080, SPA fallback) |
| `.github/ssh/known_hosts` | create | pinned VPS host keys |
| `.github/workflows/ci-cd.yml` | create | checks → image → deploy |

---

## Part A — Backend repo (`dmc-268-api-t3`)

All Part A paths are relative to `/Users/bogdanyakovenko/IdeaProjects/larchanka-training/review-bot-main/dmc-268-api-t3`.

### Task 1: Branch from latest `main`

**Files:** none (git only). This plan file is currently untracked in the working tree; it travels with the switch.

**Interfaces:** Produces branch `feature/devops-cd` based on `origin/main` (`5eea2da` or later).

- [ ] **Step 1: Check the working tree.** Run `git status --short`. Expected: ` M .gitignore` (a local-only `/local-notes/` line — leave it unstaged, never commit it), `?? .claude/`, `?? docs/superpowers/plans/2026-10-06-devops-cd-pipeline.md`. Anything else: stop and ask the user.

- [ ] **Step 2: Create the branch.**

```bash
git fetch origin
git switch -c feature/devops-cd origin/main
```

Expected: `Switched to a new branch 'feature/devops-cd'`; `git status --short` shows the same three entries.

- [ ] **Step 3: Commit the plan.**

```bash
git add docs/superpowers/plans/2026-10-06-devops-cd-pipeline.md
git commit -m "docs: add sprint-2 DevOps implementation plan"
```

### Task 2: mypy strict + pre-commit hooks

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Create: `.pre-commit-config.yaml`
- Modify: `tests/llm/test_schemas.py:26,40,71`, `tests/llm/test_eurorouter_provider.py:13`
- Modify (auto-fixed by hooks): `docs/QA/TEST_PLAN.md`, `alembic/README`
- Modify: `README.md` (Code quality section)

**Interfaces:**
- Produces: `uv run mypy` (no args, reads `files` from config) and `uv run pre-commit run --all-files` both exit 0. CI (Task 8) calls exactly these.

- [ ] **Step 1: Add the tools.**

```bash
uv add --dev "mypy>=2.4" "pre-commit>=4.6"
```

Expected: `pyproject.toml` `[dependency-groups] dev` gains `mypy>=2.4` and `pre-commit>=4.6`; `uv.lock` updated.

- [ ] **Step 2: Add mypy config.** Append to `pyproject.toml` (after the `[tool.ruff.lint.per-file-ignores]` block, before `[tool.pylint.main]`):

```toml
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
files = ["app", "tests", "alembic"]
# tests/ has no __init__.py; without this mypy doesn't name those modules
# "tests.*" and the override below never matches.
explicit_package_bases = true

[[tool.mypy.overrides]]
# Test functions stay unannotated, matching the existing test style.
module = "tests.*"
disallow_untyped_defs = false
disallow_incomplete_defs = false
disallow_untyped_calls = false
```

- [ ] **Step 3: Run mypy to see the failures.**

Run: `uv run mypy`
Expected: FAIL, `Found 4 errors in 2 files (checked 40 source files)` — three `[call-arg]` in `tests/llm/test_schemas.py` (lines 26, 40, 71: tests that deliberately pass bad kwargs) and one `[type-arg]` at `tests/llm/test_eurorouter_provider.py:13` (bare `dict`).

- [ ] **Step 4: Fix them.**

In `tests/llm/test_eurorouter_provider.py` line 13:

```python
def _chat_response(content: str) -> dict[str, object]:
```

In `tests/llm/test_schemas.py` — these tests intentionally violate the model's signature, so silence mypy on exactly those lines:

```python
        Finding(severity=FindingSeverity.INFO, message="Note")  # type: ignore[call-arg]
```

```python
            unexpected="oops",  # type: ignore[call-arg]
```

```python
        ReviewResult(findings=[], unexpected="oops")  # type: ignore[call-arg]
```

- [ ] **Step 5: Verify mypy passes.**

Run: `uv run mypy`
Expected: `Success: no issues found in 40 source files`. Also run `uv run mypy .` — expected same success (DoD wording is `mypy .`; hidden dirs such as `.claude/` are skipped).

- [ ] **Step 6: Create `.pre-commit-config.yaml`.**

```yaml
# Install once per clone: uv run pre-commit install
# CI runs the same hooks: uv run pre-commit run --all-files
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
        args: [--markdown-linebreak-ext=md]
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files

  # Local hooks run the tool versions pinned in uv.lock, so a hook, CI and a
  # manual `uv run ...` can never disagree.
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check (auto-fix)
        entry: uv run ruff check --fix --force-exclude
        language: system
        types: [python]
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format --force-exclude
        language: system
        types: [python]
      - id: mypy
        name: mypy (strict)
        entry: uv run mypy
        language: system
        types: [python]
        pass_filenames: false
      - id: pylint
        name: pylint
        entry: uv run pylint app
        language: system
        types: [python]
        pass_filenames: false
```

- [ ] **Step 7: Run all hooks once (the fixers will edit two files).**

Run: `uv run pre-commit run --all-files`
Expected first run: `fix end of files....Failed` with `Fixing docs/QA/TEST_PLAN.md` and `Fixing alembic/README`; every other hook `Passed`.
Run it again. Expected: every hook `Passed`.

- [ ] **Step 8: Install the git hook locally.** Run: `uv run pre-commit install`. Expected: `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 9: Update `README.md`.** Replace the `- Pylint — static analysis` stack bullet with these three bullets:

```markdown
- Pylint — static analysis
- mypy (strict, pydantic plugin) — type checking
- pre-commit — git hooks that auto-fix (ruff, ruff format, whitespace) and run mypy + pylint before every commit
```

Replace the whole `## Code quality` section body with:

````markdown
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
````

- [ ] **Step 10: Commit** (the hook now runs on this commit too).

```bash
git add pyproject.toml uv.lock .pre-commit-config.yaml tests/llm/test_schemas.py tests/llm/test_eurorouter_provider.py docs/QA/TEST_PLAN.md alembic/README README.md
git commit -m "chore: add strict mypy and pre-commit hooks"
```

### Task 3: Make DB credentials URL-safe

**Files:**
- Create: `tests/test_config.py`
- Modify: `app/config.py`, `alembic/env.py:10`

**Interfaces:**
- Consumes: `Settings`, `load_settings()` from `app/config.py`.
- Produces: `Settings.database_url` with `quote_plus`-escaped user/password. Any password from GitHub Secrets works in the app and in Alembic.

- [ ] **Step 1: Write the failing test** — `tests/test_config.py`:

```python
from dataclasses import replace

from sqlalchemy.engine import make_url

from app.config import load_settings


def test_database_url_escapes_special_characters_in_credentials():
    settings = replace(load_settings(), postgres_user="app@team", postgres_password="p@ss:w/rd%")

    url = make_url(settings.database_url)

    assert url.username == "app@team"
    assert url.password == "p@ss:w/rd%"
    assert url.host == settings.postgres_host
    assert url.database == settings.postgres_db
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (username/password/host parsed wrongly from the unescaped URL).

- [ ] **Step 3: Implement.** In `app/config.py` add `from urllib.parse import quote_plus` after `import os`, and change `database_url` to:

```python
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{quote_plus(self.postgres_user)}:"
            f"{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
```

In `alembic/env.py` replace line 10 with:

```python
# Alembic's config is a ConfigParser: a literal "%" (e.g. from a URL-escaped
# password) must be doubled or set_main_option raises "invalid interpolation".
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
```

- [ ] **Step 4: Run tests.**

Run: `uv run pytest -q`
Expected: all pass (`44 passed`).

- [ ] **Step 5: Commit.**

```bash
git add app/config.py alembic/env.py tests/test_config.py
git commit -m "fix: URL-escape database credentials for app and alembic"
```

### Task 4: Redis queue + RQ worker

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `app/config.py`, `tests/test_config.py`
- Create: `app/queue.py`, `app/worker/__init__.py` (empty), `app/worker/tasks.py`, `app/worker/__main__.py`, `app/worker/smoke.py`, `tests/worker/test_worker.py`

**Interfaces:**
- Consumes: `settings` from `app/config.py`.
- Produces (used by Tasks 5, 7, 8, 10):
  - `Settings.redis_url: str` — env `REDIS_URL`, default `redis://redis:6379/0`.
  - `app.queue.QUEUE_NAME = "default"`, `get_redis(url: str | None = None) -> Redis`, `get_queue(connection: Redis | None = None) -> Queue`.
  - `app.worker.tasks.ping(message: str) -> str` returning `f"pong: {message}"`.
  - `python -m app.worker` — long-running RQ worker on `QUEUE_NAME`.
  - `python -m app.worker.smoke` — exit 0 if a real worker ran `ping("smoke")` within 30 s, else exit 1. `run_smoke(queue: Queue | None = None, timeout_seconds: float = 30.0) -> bool`.

- [ ] **Step 1: Add dependencies.**

```bash
uv add "rq>=2.12" "redis>=8.1"
uv add --dev "fakeredis>=2.39"
```

- [ ] **Step 2: Write failing config tests.** Append to `tests/test_config.py`:

```python
def test_redis_url_defaults_to_compose_service(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert load_settings().redis_url == "redis://redis:6379/0"


def test_redis_url_reads_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6380/2")

    assert load_settings().redis_url == "redis://example:6380/2"
```

- [ ] **Step 3: Write failing worker tests** — `tests/worker/test_worker.py`:

```python
from fakeredis import FakeRedis
from rq import Queue, SimpleWorker

from app.queue import QUEUE_NAME, get_queue
from app.worker.smoke import run_smoke
from app.worker.tasks import ping


def test_ping_returns_pong():
    assert ping("hello") == "pong: hello"


def test_get_queue_uses_default_queue_name():
    assert get_queue(FakeRedis()).name == QUEUE_NAME


def test_worker_processes_enqueued_job():
    connection = FakeRedis()
    queue = get_queue(connection)
    job = queue.enqueue(ping, "hello")

    SimpleWorker([queue], connection=connection).work(burst=True)

    assert job.get_status(refresh=True) == "finished"
    assert job.return_value() == "pong: hello"


def test_run_smoke_succeeds_when_job_runs():
    # is_async=False executes the job inline at enqueue time, standing in for a worker.
    queue = Queue(QUEUE_NAME, connection=FakeRedis(), is_async=False)

    assert run_smoke(queue, timeout_seconds=5) is True


def test_run_smoke_fails_when_no_worker_picks_up_the_job():
    queue = get_queue(FakeRedis())

    assert run_smoke(queue, timeout_seconds=0.6) is False
```

- [ ] **Step 4: Run to verify failure.**

Run: `uv run pytest tests/test_config.py tests/worker -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'redis_url'` and `ModuleNotFoundError: No module named 'app.queue'`.

- [ ] **Step 5: Implement config.** In `app/config.py` add the field after `eurorouter_model: str`:

```python
    redis_url: str
```

and in `load_settings()` after the `eurorouter_model=` line:

```python
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
```

- [ ] **Step 6: Implement `app/queue.py`.**

```python
from redis import Redis
from rq import Queue

from app.config import settings

QUEUE_NAME = "default"


def get_redis(url: str | None = None) -> Redis:
    # Redis.from_url does not connect until the first command, so importing
    # this module (or starting the API) never needs a reachable Redis.
    return Redis.from_url(url or settings.redis_url)


def get_queue(connection: Redis | None = None) -> Queue:
    return Queue(QUEUE_NAME, connection=connection or get_redis())
```

- [ ] **Step 7: Implement the worker package.** Create empty `app/worker/__init__.py`, then `app/worker/tasks.py`:

```python
def ping(message: str) -> str:
    # Placeholder job proving the enqueue -> Redis -> worker path end to end.
    # Real review jobs arrive with issue #13.
    return f"pong: {message}"
```

`app/worker/__main__.py`:

```python
from rq import Worker

from app.queue import get_queue, get_redis


def main() -> None:
    connection = get_redis()
    Worker([get_queue(connection)], connection=connection).work()


if __name__ == "__main__":
    main()
```

`app/worker/smoke.py`:

```python
import sys
import time

from rq import Queue

from app.queue import get_queue
from app.worker.tasks import ping


def run_smoke(queue: Queue | None = None, timeout_seconds: float = 30.0) -> bool:
    job = (queue or get_queue()).enqueue(ping, "smoke")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if job.get_status(refresh=True) == "finished":
            return bool(job.return_value() == "pong: smoke")
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    sys.exit(0 if run_smoke() else 1)
```

- [ ] **Step 8: Run tests and all checks.**

Run: `uv run pytest -q`
Expected: `51 passed`.
Run: `uv run pre-commit run --all-files`
Expected: all hooks `Passed` (pylint `10.00/10`, mypy `Success`).

- [ ] **Step 9: Commit.**

```bash
git add pyproject.toml uv.lock app/config.py app/queue.py app/worker tests/test_config.py tests/worker
git commit -m "feat: add Redis-backed RQ worker with smoke check"
```

### Task 5: Local stack — Redis, worker, migrations in Compose

**Files:**
- Modify: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: `python -m app.worker`, `python -m app.worker.smoke` (Task 4), `alembic upgrade head`.
- Produces: `docker compose up` starts `postgres`, `redis`, `migrate` (exits 0), `api`, `worker`. The image contains `alembic.ini` + `alembic/` (needed by Task 7's `migrate` container).

- [ ] **Step 1: Ship Alembic in the image.** In `Dockerfile` replace `COPY app ./app` with:

```dockerfile
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
```

Append one line to `.dockerignore`: `infra`.

- [ ] **Step 2: Replace `docker-compose.yml`.**

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - path: .env
        required: false
    environment: &app-env
      POSTGRES_DB: ${POSTGRES_DB:-app}
      POSTGRES_USER: ${POSTGRES_USER:-app}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-app}
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      REDIS_URL: redis://redis:6379/0
      OLLAMA_HOST: ${OLLAMA_HOST:-http://host.docker.internal:11434}
    depends_on: &app-deps
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy

  # Same image as the API, different command.
  worker:
    build: .
    command: ["python", "-m", "app.worker"]
    env_file:
      - path: .env
        required: false
    environment: *app-env
    depends_on: *app-deps

  # One-shot: applies migrations, then exits; api/worker wait for it.
  migrate:
    build: .
    command: ["alembic", "upgrade", "head"]
    restart: "no"
    environment: *app-env
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-app}
      POSTGRES_USER: ${POSTGRES_USER:-app}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-app}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-app}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:8-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
  redis_data:
```

- [ ] **Step 3: Add to `.env.example`** after `POSTGRES_PORT=5432`:

```
REDIS_URL=redis://redis:6379/0
```

- [ ] **Step 4: Verify the whole stack with one command.**

```bash
docker compose up -d --build --wait --wait-timeout 180
docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}'
curl -s -w ' %{http_code}\n' http://localhost:8000/healthcheck
docker compose exec -T api python -m app.worker.smoke; echo "smoke exit=$?"
docker compose exec -T postgres psql -U app -tc "select version_num from alembic_version"
```

Expected: `api running 0`, `worker running 0`, `migrate exited 0`, `postgres running 0`, `redis running 0`; `{"status":"ok"} 200`; `smoke exit=0`; `fb6eae330c65`. `docker compose logs worker` shows `Successfully completed app.worker.tasks.ping('smoke')`.

- [ ] **Step 5: Verify a URL-hostile password works (Review Focus 1).**

```bash
docker compose down -v
POSTGRES_PASSWORD='p@ss%w0rd' docker compose up -d --build --wait --wait-timeout 180
docker compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' | grep migrate
docker compose down -v
```

Expected: `migrate exited 0`; `--wait` succeeds.

- [ ] **Step 6: Update `README.md`.**
  - Stack list: add `- Redis + [RQ](https://python-rq.org/) — background job queue and worker`.
  - Rename `## Run the full local environment (API + PostgreSQL)` to `## Run the full local environment` and replace its body with:

````markdown
```bash
docker compose up
```

Starts PostgreSQL, Redis, a one-shot `migrate` container (`alembic upgrade head`), the API and the worker. The API is at http://localhost:8000/healthcheck once the stack is up. Check that the worker consumes jobs:

```bash
docker compose exec api python -m app.worker.smoke   # exit code 0 = OK
```

Ollama is expected to run externally; point `OLLAMA_HOST` at it (see `.env.example`). Put Eurorouter credentials in `.env` (`EUROROUTER_*`) — it is read by the api and worker containers if present.
````

  - Environment variables table: add rows

```markdown
| `REDIS_URL` | Redis connection URL (queue) | `redis://redis:6379/0` |
| `EUROROUTER_BASE_URL` | Eurorouter API base URL | empty |
| `EUROROUTER_API_KEYS` | Comma-separated Eurorouter API keys | empty |
| `EUROROUTER_MODEL` | Eurorouter model name | `gpt-4o-mini` |
```

- [ ] **Step 7: Commit.**

```bash
git add Dockerfile .dockerignore docker-compose.yml .env.example README.md
git commit -m "feat: run redis, worker and migrations in docker compose"
```

### Task 6: Record the Redis decision in the design docs

**Files:** Modify `SYSTEM_DESIGN.md`, `docs/QA/TEST_PLAN.md`, `BACKEND_ARCHITECTURE.md`.

**Interfaces:** none (docs). Teammates authored SYSTEM_DESIGN/TEST_PLAN — the PR description (Task 11) must call this change out for the team lead.

- [ ] **Step 1: `SYSTEM_DESIGN.md` replacements** (exact strings):
  - `- Reviews run asynchronously through RabbitMQ workers.` → `- Reviews run asynchronously through Redis-backed (RQ) workers.`
  - `    ORCH --> MQ[(RabbitMQ)]` (2 occurrences) → `    ORCH --> MQ[(Redis / RQ)]`
  - `    S --> MQ[(RabbitMQ)]` → `    S --> MQ[(Redis / RQ)]`
  - `**RabbitMQ is the v1 queue. Redis is not used in v1.** A future cache/locking/rate-limiting layer may use Redis behind abstractions.` → `**Redis with [RQ](https://python-rq.org/) is the v1 queue** (changed from RabbitMQ on 2026-10-06 by team decision: the sprint tasks specify Redis, and one Redis container is simpler to operate than a broker). The same Redis may later back a cache/locking/rate-limiting layer behind abstractions.`
  - `large diffs/context are not sent through RabbitMQ.` → `large diffs/context are not sent through the queue.`
  - `    participant Q as RabbitMQ` → `    participant Q as Redis (RQ)`
  - `Exhausted messages go to a DLQ.` → `Exhausted jobs stay in RQ's \`FailedJobRegistry\`, which serves as the DLQ; retries use \`rq.Retry(max=..., interval=[...])\`.`
  - `- RabbitMQ retry/backoff, concurrency and stale-run recovery;` → `- RQ retry/backoff, concurrency and stale-run recovery;`
  - `- RabbitMQ queue semantics and retry policy agreed.` → `- RQ queue semantics and retry policy agreed.`

- [ ] **Step 2: `docs/QA/TEST_PLAN.md` replacements:**
  - ``- RabbitMQ: publication of `{run_id}`, at-least-once delivery, worker behavior on duplicate message, retries, DLQ.`` → ``- Redis/RQ: enqueue of `{run_id}`, worker behavior on a duplicate job, retries, failed-job registry (DLQ).``
  - `(PostgreSQL, RabbitMQ)` → `(PostgreSQL, Redis)`
  - `Testcontainers (Postgres, RabbitMQ), mock LLM` → `Testcontainers (Postgres, Redis), mock LLM`
  - `- **Testcontainers** — Postgres, RabbitMQ.` → `- **Testcontainers** — Postgres, Redis.`
  - `| RabbitMQ / Worker |` → `| Redis (RQ) / Worker |`

- [ ] **Step 3: Append to `BACKEND_ARCHITECTURE.md`:**

```markdown

## Queue and worker

Background work goes through Redis with [RQ](https://python-rq.org/):

- `app/queue.py` — `get_redis()` / `get_queue()`. Code enqueues with `get_queue().enqueue(func, *args)`; the job function must live in an importable module (not `__main__`).
- `app/worker/` — the worker process, `python -m app.worker`, run from the same image as the API with a different command. `tasks.py` holds job functions; `ping` is a placeholder until review jobs land (issue #13).
- `python -m app.worker.smoke` enqueues `ping` and waits for its result — CI and the deploy pipeline use it to prove API → Redis → worker works.
```

- [ ] **Step 4: Verify.** Run: `git grep -n RabbitMQ`. Expected: exactly one hit — the "changed from RabbitMQ on 2026-10-06" sentence in `SYSTEM_DESIGN.md`. Run `uv run pre-commit run --all-files` → all `Passed`.

- [ ] **Step 5: Commit.**

```bash
git add SYSTEM_DESIGN.md docs/QA/TEST_PLAN.md BACKEND_ARCHITECTURE.md
git commit -m "docs: switch v1 queue from RabbitMQ to Redis/RQ"
```

### Task 7: Terraform for the server environment

**Files:** Create `infra/versions.tf`, `infra/variables.tf`, `infra/main.tf`, `infra/outputs.tf`, `infra/Caddyfile`, `infra/apply.sh`, `infra/deploy.sh`, `infra/.gitignore`, `infra/.terraform.lock.hcl` (generated).

**Interfaces:**
- Consumes: backend image with `alembic.ini`, `alembic/` and `python -m app.worker` (Tasks 4–5 — without `alembic.ini` in the image the migrate container fails with `No 'script_location' key found`).
- Produces (used by Tasks 8, 10, 13):
  - `infra/apply.sh <terraform args>` — runs Terraform 1.16.5 in a container against the host Docker socket, in `infra/`, as the invoking user.
  - `infra/deploy.sh` (no args) — the only command deploy jobs run on the server: `flock ../deploy.lock`, `init`, migrations-only targeted apply, full apply. Exit ≠ 0 = deploy failed, running containers untouched if migrations failed.
  - Variables: `api_image` (required), `postgres_password` (required, sensitive), `web_image` (default `nginxinc/nginx-unprivileged:1.30-alpine`), `eurorouter_api_keys`, `eurorouter_base_url`, `eurorouter_model`, `http_port` (default 80). Loaded on the server from `api.auto.tfvars.json` (backend deploy) and `web.auto.tfvars.json` (frontend deploy).
  - Containers `dmc268-postgres`, `dmc268-redis`, `dmc268-migrate`, `dmc268-api`, `dmc268-worker`, `dmc268-web`, `dmc268-caddy`; network aliases `postgres`, `redis`, `api`, `web`; web image must serve HTTP on port **8080**.
  - Routing: `http://<host>/api/<path>` → `api:8000/<path>`; everything else → `web:8080`.

- [ ] **Step 1: Create `infra/versions.tf`.**

```hcl
terraform {
  required_version = ">= 1.16.0"

  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 4.6"
    }
  }
}

# Terraform runs on the VPS itself (see apply.sh), so it talks to the local
# Docker daemon and keeps its state next to this file.
provider "docker" {
  host = "unix:///var/run/docker.sock"
}
```

- [ ] **Step 2: Create `infra/variables.tf`.**

```hcl
variable "api_image" {
  description = "Backend image (API, worker and migrations), e.g. ghcr.io/larchanka-training/dmc-268-api-t3:<sha>."
  type        = string
}

variable "web_image" {
  description = "Frontend static image, written by the dmc-268-ui-t3 deploy. The placeholder serves until the first UI deploy."
  type        = string
  default     = "nginxinc/nginx-unprivileged:1.30-alpine"
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "eurorouter_api_keys" {
  description = "Comma-separated Eurorouter API keys."
  type        = string
  sensitive   = true
  default     = ""
}

variable "eurorouter_base_url" {
  type    = string
  default = ""
}

variable "eurorouter_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "http_port" {
  description = "Host port the public entry (Caddy) binds to."
  type        = number
  default     = 80
}
```

- [ ] **Step 3: Create `infra/main.tf`.**

```hcl
locals {
  prefix = "dmc268"

  app_env = [
    "POSTGRES_DB=app",
    "POSTGRES_USER=app",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_HOST=postgres",
    "POSTGRES_PORT=5432",
    "REDIS_URL=redis://redis:6379/0",
    "EUROROUTER_BASE_URL=${var.eurorouter_base_url}",
    "EUROROUTER_API_KEYS=${var.eurorouter_api_keys}",
    "EUROROUTER_MODEL=${var.eurorouter_model}",
  ]
}

resource "docker_network" "app" {
  name = local.prefix
}

resource "docker_volume" "postgres_data" {
  name = "${local.prefix}_postgres_data"
}

resource "docker_volume" "redis_data" {
  name = "${local.prefix}_redis_data"
}

resource "docker_volume" "caddy_data" {
  name = "${local.prefix}_caddy_data"
}

resource "docker_image" "postgres" {
  name         = "postgres:16-alpine"
  keep_locally = true
}

resource "docker_image" "redis" {
  name         = "redis:8-alpine"
  keep_locally = true
}

resource "docker_image" "caddy" {
  name         = "caddy:2.10-alpine"
  keep_locally = true
}

# App images are pulled by the deploy workflows (GHCR needs auth), so these
# find them locally instead of pulling. keep_locally leaves old tags for rollback.
resource "docker_image" "api" {
  name         = var.api_image
  keep_locally = true
}

resource "docker_image" "web" {
  name         = var.web_image
  keep_locally = true
}

resource "docker_container" "postgres" {
  name    = "${local.prefix}-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"
  env = [
    "POSTGRES_DB=app",
    "POSTGRES_USER=app",
    "POSTGRES_PASSWORD=${var.postgres_password}",
  ]

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["postgres"]
  }

  volumes {
    volume_name    = docker_volume.postgres_data.name
    container_path = "/var/lib/postgresql/data"
  }

  healthcheck {
    test     = ["CMD-SHELL", "pg_isready -U app -d app"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 120
}

resource "docker_container" "redis" {
  name    = "${local.prefix}-redis"
  image   = docker_image.redis.image_id
  restart = "unless-stopped"

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["redis"]
  }

  volumes {
    volume_name    = docker_volume.redis_data.name
    container_path = "/data"
  }

  healthcheck {
    test     = ["CMD", "redis-cli", "ping"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 60
}

# Forces the migrate container to be re-created on every apply, so
# `alembic upgrade head` runs on each deploy (a no-op when the schema is current).
resource "terraform_data" "every_apply" {
  triggers_replace = timestamp()
}

resource "docker_container" "migrate" {
  name     = "${local.prefix}-migrate"
  image    = docker_image.api.image_id
  command  = ["alembic", "upgrade", "head"]
  env      = local.app_env
  must_run = false
  attach   = true
  logs     = true

  networks_advanced {
    name = docker_network.app.id
  }

  depends_on = [docker_container.postgres]

  lifecycle {
    replace_triggered_by = [terraform_data.every_apply]

    # api and worker depend on this resource, so a failed migration stops the
    # apply before new code starts against the old schema.
    postcondition {
      condition     = self.exit_code == 0
      error_message = "alembic upgrade head failed; see the migrate container logs above."
    }
  }
}

resource "docker_container" "api" {
  name    = "${local.prefix}-api"
  image   = docker_image.api.image_id
  restart = "unless-stopped"
  # Caddy strips /api before proxying; --root-path makes /api/docs resolve.
  command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api"]
  env     = local.app_env

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["api"]
  }

  healthcheck {
    test     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthcheck')"]
    interval = "5s"
    timeout  = "5s"
    retries  = 10
  }

  wait         = true
  wait_timeout = 120

  depends_on = [docker_container.migrate, docker_container.redis]
}

resource "docker_container" "worker" {
  name    = "${local.prefix}-worker"
  image   = docker_image.api.image_id
  restart = "unless-stopped"
  command = ["python", "-m", "app.worker"]
  env     = local.app_env

  networks_advanced {
    name = docker_network.app.id
  }

  depends_on = [docker_container.migrate, docker_container.redis]
}

resource "docker_container" "web" {
  name    = "${local.prefix}-web"
  image   = docker_image.web.image_id
  restart = "unless-stopped"

  networks_advanced {
    name    = docker_network.app.id
    aliases = ["web"]
  }
}

# The only container with a published port: plain HTTP on the server IP
# (no domain, so no TLS certificate).
resource "docker_container" "caddy" {
  name    = "${local.prefix}-caddy"
  image   = docker_image.caddy.image_id
  restart = "unless-stopped"

  ports {
    internal = 80
    external = var.http_port
  }

  upload {
    content = file("${path.module}/Caddyfile")
    file    = "/etc/caddy/Caddyfile"
  }

  networks_advanced {
    name = docker_network.app.id
  }

  volumes {
    volume_name    = docker_volume.caddy_data.name
    container_path = "/data"
  }

  depends_on = [docker_container.api, docker_container.web]
}
```

- [ ] **Step 4: Create `infra/outputs.tf`, `infra/Caddyfile`, `infra/.gitignore`, `infra/apply.sh`.**

`infra/outputs.tf`:

```hcl
output "api_image" {
  value = var.api_image
}

output "web_image" {
  value = var.web_image
}
```

`infra/Caddyfile` (tabs for indentation, as Caddy's formatter expects):

```
:80 {
	encode gzip

	# /api/healthcheck -> api:8000/healthcheck
	handle_path /api/* {
		reverse_proxy api:8000
	}

	handle {
		reverse_proxy web:8080
	}
}
```

`infra/.gitignore`:

```
.terraform/
*.tfstate
*.tfstate.*
*.auto.tfvars.json
```

`infra/apply.sh`:

```sh
#!/bin/sh
# Runs Terraform in a container against this host's Docker daemon. Runs as the
# invoking user (plus the docker socket's group) so state files stay owned by
# that user rather than root.
set -eu
cd "$(dirname "$0")"
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --group-add "$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 0)" \
  -e HOME=/tmp \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD":/workspace \
  -w /workspace \
  hashicorp/terraform:1.16.5 "$@"
```

`infra/deploy.sh`:

```sh
#!/bin/sh
# Server-side deploy entrypoint, called over SSH by both repos' deploy jobs.
# flock serialises them: they apply the same Terraform state.
set -eu
cd "$(dirname "$0")"
exec flock ../deploy.lock sh -euc '
  ./apply.sh init -input=false -no-color
  # Migrations first, on their own: in a single apply Terraform stops the old
  # API before it checks the migration result, so a failed migration would
  # take the site down instead of just failing the deploy.
  ./apply.sh apply -input=false -auto-approve -no-color -target=docker_container.migrate
  ./apply.sh apply -input=false -auto-approve -no-color
'
```

Make both executable and tracked as such:

```bash
chmod +x infra/apply.sh infra/deploy.sh
```

(The `|| echo 0` fallback in `apply.sh` covers macOS, where `stat -c` doesn't exist and Docker Desktop's socket is `root:root 660` inside the VM. Terraform prints a "Resource targeting is in effect" warning for the first apply of `deploy.sh` — expected.)

- [ ] **Step 5: Format, init, lock, validate.**

```bash
infra/apply.sh fmt -check -diff
infra/apply.sh init -input=false
infra/apply.sh providers lock -platform=linux_amd64 -platform=linux_arm64
infra/apply.sh validate
```

Expected: `fmt` prints nothing (exit 0); `Terraform has been successfully initialized!`; lock written; `Success! The configuration is valid.` `infra/.terraform.lock.hcl` now exists.

- [ ] **Step 6: Rehearse locally on Docker Desktop + persistence check (Review Focus 1 + 3).**

```bash
docker build -t dmc268-api:local .
cat > infra/local.auto.tfvars.json <<'EOF'
{"api_image": "dmc268-api:local", "postgres_password": "p@ss%w0rd", "http_port": 18080}
EOF
infra/apply.sh apply -auto-approve -input=false
curl -s -w ' %{http_code}\n' http://localhost:18080/api/healthcheck
curl -s -o /dev/null -w 'root %{http_code}\n' http://localhost:18080/
curl -s http://localhost:18080/api/docs | grep -o "url: '[^']*'"
docker exec dmc268-api python -m app.worker.smoke; echo "smoke exit=$?"
docker exec dmc268-postgres psql -U app -d app -c 'create table persist_check (x int); insert into persist_check values (1)'
infra/apply.sh apply -auto-approve -input=false
docker exec dmc268-postgres psql -U app -d app -tc 'select x from persist_check'
```

Expected: first apply `Apply complete! Resources: 17 added`; `{"status":"ok"} 200`; `root 200` (nginx placeholder); `url: '/api/openapi.json'`; `smoke exit=0`; second apply `Apply complete! Resources: 2 added, 0 changed, 2 destroyed.` (only `every_apply` + `migrate` re-created); final select prints `1`.

- [ ] **Step 7: Failed migration must not replace the running API (Review Focus 2).** macOS has no `flock`, so run `deploy.sh` inside a `docker:cli` container (busybox `flock` + Docker CLI), mounting the repo at the same path so `apply.sh`'s volume paths resolve on the host daemon. `python:3.12-slim` stands in for a broken release (it has no `alembic`).

```bash
deploy() { docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD":"$PWD" -w "$PWD" docker:cli infra/deploy.sh; }
deploy 2>&1 | grep -E 'Apply complete|Error'
docker image pull -q python:3.12-slim
sed -i '' 's#"dmc268-api:local"#"python:3.12-slim"#' infra/local.auto.tfvars.json
deploy > /tmp/bad-deploy.log 2>&1; echo "deploy exit=$?"; grep -m2 Error /tmp/bad-deploy.log
docker inspect -f '{{.Config.Image}}' dmc268-api
curl -s -w ' %{http_code}\n' http://localhost:18080/api/healthcheck
sed -i '' 's#"python:3.12-slim"#"dmc268-api:local"#' infra/local.auto.tfvars.json
deploy 2>&1 | grep -E 'Apply complete|Error'
docker exec dmc268-api python -m app.worker.smoke; echo "smoke exit=$?"
```

Expected: first deploy two `Apply complete!` lines; bad deploy `deploy exit=1` with `exec: "alembic": executable file not found` and `Resource postcondition failed`; `dmc268-api` still runs the **old** image id (not `python:3.12-slim`) and healthcheck is still `{"status":"ok"} 200`; recovery deploy two `Apply complete!`; `smoke exit=0`.
For contrast (do not keep): a plain `infra/apply.sh apply -var api_image=python:3.12-slim` removes `dmc268-api` and the healthcheck returns `502` — that is why deploys go through `deploy.sh`.

- [ ] **Step 8: Tear down the rehearsal.**

```bash
infra/apply.sh destroy -auto-approve -input=false
rm infra/local.auto.tfvars.json infra/terraform.tfstate infra/terraform.tfstate.backup
docker image rm dmc268-api:local
```

Expected: `Destroy complete!`; `git status --short infra` lists only the files from Steps 1–5 (`.terraform/` is ignored).

- [ ] **Step 9: Commit.**

```bash
git add infra/versions.tf infra/variables.tf infra/main.tf infra/outputs.tf infra/Caddyfile infra/apply.sh infra/deploy.sh infra/.gitignore infra/.terraform.lock.hcl
git commit -m "feat: add Terraform for postgres, redis, api, worker, web and caddy"
```

### Task 8: CI checks workflow

**Files:** Create `.github/workflows/ci-cd.yml` (checks only; Task 10 appends the image/deploy jobs).

**Interfaces:**
- Consumes: `uv run pre-commit run --all-files` (Task 2), `uv run pytest`, `infra/apply.sh` (Task 7), `python -m app.worker.smoke` (Task 4), compose stack (Task 5).
- Produces: job ids `checks`, `terraform`, `compose` — Task 10's `image` job `needs: [checks, terraform, compose]`.

- [ ] **Step 1: Create `.github/workflows/ci-cd.yml`.**

```yaml
name: CI/CD

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

env:
  IMAGE: ghcr.io/${{ github.repository }}

jobs:
  checks:
    name: Lint, types, tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
      - run: uv sync --locked
      # Same hooks as the local git hook: ruff, ruff format, mypy, pylint, whitespace.
      - run: uv run pre-commit run --all-files --show-diff-on-failure
      - run: uv run pytest

  terraform:
    name: Terraform fmt + validate
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7
      - run: infra/apply.sh fmt -check -diff
      - run: infra/apply.sh init -backend=false -input=false -no-color
      - run: infra/apply.sh validate -no-color

  compose:
    name: docker compose smoke
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7
      - run: docker compose up -d --build --wait --wait-timeout 180
      - run: curl -fsS http://localhost:8000/healthcheck
      - run: docker compose exec -T api python -m app.worker.smoke
      - if: failure()
        run: docker compose logs
      - if: always()
        run: docker compose down -v
```

- [ ] **Step 2: Validate YAML locally.** Run: `uv run pre-commit run check-yaml --all-files`. Expected: `Passed`.

- [ ] **Step 3: Commit.** (CI first runs when the branch is pushed in Task 9.)

```bash
git add .github/workflows/ci-cd.yml
git commit -m "ci: add lint, type, test, terraform and compose checks"
```

### Task 9: Server bootstrap (one-time, idempotent)

**Files:** Create `infra/bootstrap/bootstrap.sh`, `infra/bootstrap/deploy_key.pub`, `.github/ssh/known_hosts`, `.github/workflows/bootstrap.yml`.

**Interfaces:**
- Consumes: org secrets `VPS_DMC268_U` (assumed `root`; script refuses otherwise), `VPS_DMC268_P`; org var `VPS_DMC268_IP_T3`; repo secret `DEPLOY_SSH_KEY`.
- Produces (used by Tasks 10, 13): on the VPS — Docker + compose plugin, user `deploy` (key-only, `docker` group) accepting `DEPLOY_SSH_KEY`, `/opt/dmc268` (`0750`, owned by `deploy`), LLMNR/mDNS off. In the repo — `.github/ssh/known_hosts` pinning the VPS host keys.

- [ ] **Step 1 (USER, outward-facing — get go-ahead): create the deploy key and secrets.** Run on the laptop:

```bash
ssh-keygen -t ed25519 -N '' -C 'dmc268-t3-deploy' -f ~/.ssh/dmc268_t3_deploy
gh secret set DEPLOY_SSH_KEY -R larchanka-training/dmc-268-api-t3 < ~/.ssh/dmc268_t3_deploy
gh secret set DEPLOY_SSH_KEY -R larchanka-training/dmc-268-ui-t3 < ~/.ssh/dmc268_t3_deploy
gh secret set POSTGRES_PASSWORD -R larchanka-training/dmc-268-api-t3 --body "$(openssl rand -hex 32)"
cp ~/.ssh/dmc268_t3_deploy.pub infra/bootstrap/deploy_key.pub
gh secret list -R larchanka-training/dmc-268-api-t3
```

Expected: last command lists `DEPLOY_SSH_KEY` and `POSTGRES_PASSWORD`. Keep `~/.ssh/dmc268_t3_deploy` (lets the team `ssh -i ~/.ssh/dmc268_t3_deploy deploy@77.237.236.234` for debugging). **Never rotate `POSTGRES_PASSWORD` after the first deploy** without also running `ALTER USER app PASSWORD ...` inside `dmc268-postgres` — Postgres only reads it when the volume is first created.

- [ ] **Step 2: Pin the host keys.** Create `.github/ssh/known_hosts` with exactly these lines (captured 2026-10-06 via `ssh-keyscan 77.237.236.234`; ED25519 fingerprint `SHA256:PzPM4kjQnWI2uklVXm3Ss/YTCYCXrv+IStkLGYA1hVM`):

```
77.237.236.234 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXpGMsC4sBPxe8nY/P8zVploMI+UHp351cHgDm+8DbZ
77.237.236.234 ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQChl9UMEW7eJPebffLNAorAoXQ17vjyjtckkkYON+LEk4wVXWIGTXFnP7jhJVdcQylc9FIZE12HIE+S6ElJhod5Db1+HK/CAdw8jvTAu38x9OBrT4mq8vw7T4a52+fX1MrcjrHCcy3VQFeMTSdxKOyPWs5Ynm59ScveTUSWQenLYFqggnBqDDabQ/rS65R3w92tF7KdEX5a9mqAW6Kx/dTwMdxkJQNVGa2dkV9tyiP8Xy2aVTM//9QWTRvV65mg4bwjyZkGm3+sMeOsMXenLeb1txpCPF2ocaqOlfERegCL+80zyTJWUdteuL6VrVbeJpmM0knlY5oZEt2p3vnk++g3/MMoK45ock2CuxK3FtcSaR3xwlF8k8GmNsFN8StF49yaqpy5L/lcES5BShCUhplkcCkK6IC5e9iE0vsNQc+cWrzQFzn/Qw5tDw+5OdC9uotHvgAJScVXkWX7rsqOEa4S3T9X9git/gFiMJU5bpmXrLKW60sNezrA9Q89mfybWlU=
77.237.236.234 ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBPqPNdpK3kK0MzwX/NL594V5R/dVWSeR9lBdob4vrXsytX9IOfwmAilxYmcMALfdz1XjUUV3AAzKVg6+yNRepLc=
```

Verify: `ssh-keyscan -t ed25519 77.237.236.234 2>/dev/null | ssh-keygen -lf - -E sha256` prints the same `SHA256:PzPM4k…` fingerprint. If it differs, stop and ask the user (the server was reinstalled or something is intercepting).

- [ ] **Step 3: Create `infra/bootstrap/bootstrap.sh`.**

```bash
#!/usr/bin/env bash
# One-time, idempotent server preparation. Run as root by
# .github/workflows/bootstrap.yml over the provider's password login; every
# later connection uses the key-only `deploy` user it creates.
set -euo pipefail

: "${DEPLOY_PUBKEY:?DEPLOY_PUBKEY must be set}"

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap must run as root (connected as $(id -un))" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
fi
# "*" is never a valid hash: no password login, key login still works.
usermod --password '*' deploy
# Membership in the docker group is root-equivalent; deploy exists only for CD.
usermod -aG docker deploy

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
touch /home/deploy/.ssh/authorized_keys
grep -qxF "$DEPLOY_PUBKEY" /home/deploy/.ssh/authorized_keys \
  || printf '%s\n' "$DEPLOY_PUBKEY" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

install -d -m 750 -o deploy -g deploy /opt/dmc268

# systemd-resolved answers LLMNR on public port 5355; other teams' VPSs had it open.
if systemctl is-active --quiet systemd-resolved; then
  install -d /etc/systemd/resolved.conf.d
  printf '[Resolve]\nLLMNR=no\nMulticastDNS=no\n' > /etc/systemd/resolved.conf.d/dmc268.conf
  systemctl restart systemd-resolved
fi

echo "bootstrap complete: docker $(docker version --format '{{.Server.Version}}'), user deploy ready"
```

`chmod +x infra/bootstrap/bootstrap.sh`, then lint it:

```bash
docker run --rm -v "$PWD":/mnt koalaman/shellcheck:stable infra/bootstrap/bootstrap.sh infra/apply.sh
```

Expected: no output (exit 0).

- [ ] **Step 4: Create `.github/workflows/bootstrap.yml`.**

```yaml
name: Bootstrap server

# Idempotent, so re-running is safe. The push trigger lets it run from the PR
# branch before this file reaches main (workflow_dispatch only works from main).
on:
  workflow_dispatch:
  push:
    paths:
      - infra/bootstrap/**
      - .github/workflows/bootstrap.yml

permissions:
  contents: read

concurrency:
  group: deploy-production
  cancel-in-progress: false

jobs:
  bootstrap:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      DEPLOY_HOST: ${{ vars.VPS_DMC268_IP_T3 }}
    steps:
      - uses: actions/checkout@v7

      - name: Install sshpass
        run: sudo apt-get update -qq && sudo apt-get install -y -qq sshpass

      - name: Run bootstrap.sh as root (password login, the only time it is used)
        env:
          SSHPASS: ${{ secrets.VPS_DMC268_P }}
          VPS_USER: ${{ secrets.VPS_DMC268_U }}
        run: |
          set -euo pipefail
          pubkey="$(cat infra/bootstrap/deploy_key.pub)"
          sshpass -e ssh \
            -o StrictHostKeyChecking=yes \
            -o UserKnownHostsFile=.github/ssh/known_hosts \
            -o PreferredAuthentications=password \
            -o PubkeyAuthentication=no \
            "$VPS_USER@$DEPLOY_HOST" "DEPLOY_PUBKEY='$pubkey' bash -s" < infra/bootstrap/bootstrap.sh

      - name: Verify key login as deploy
        env:
          DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}
        run: |
          set -euo pipefail
          install -d -m 700 ~/.ssh
          printf '%s\n' "$DEPLOY_SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          ssh -i ~/.ssh/deploy_key -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=yes -o UserKnownHostsFile=.github/ssh/known_hosts \
            "deploy@$DEPLOY_HOST" 'id && docker version --format "{{.Server.Version}}" && test -w /opt/dmc268 && echo deploy-ready'
```

- [ ] **Step 5: Commit, then (USER go-ahead) push the branch — this runs bootstrap + CI.**

```bash
git add infra/bootstrap .github/ssh/known_hosts .github/workflows/bootstrap.yml
git commit -m "ops: add one-time server bootstrap workflow"
git push -u origin feature/devops-cd
gh run watch -R larchanka-training/dmc-268-api-t3 "$(gh run list -R larchanka-training/dmc-268-api-t3 -w 'Bootstrap server' -L 1 --json databaseId --jq '.[0].databaseId')"
```

Expected: `Bootstrap server` succeeds; the last step prints `uid=…(deploy) … groups=…(docker)`, a Docker version and `deploy-ready`. `CI/CD` on the branch: `checks`, `terraform`, `compose` green.
If bootstrap fails with `bootstrap must run as root`, stop: tell the user `VPS_DMC268_U` is not root and ask how sudo works on this server. If it fails with `Host key verification failed`, redo Step 2's fingerprint check with the user.

### Task 10: Build + deploy jobs

**Files:** Modify `.github/workflows/ci-cd.yml` (append jobs), `README.md` (Deployment section).

**Interfaces:**
- Consumes: Task 7 Terraform (`infra/apply.sh`, variables), Task 8 job ids, Task 9 server state + `known_hosts`, secrets `DEPLOY_SSH_KEY`, `POSTGRES_PASSWORD`, `AI_DMC268_T3`, vars `VPS_DMC268_IP_T3`, `AI_DMC268_URL`.
- Produces (used by Task 13): `/opt/dmc268/infra/` populated with the Terraform config, `deploy.sh` and `api.auto.tfvars.json`; GitHub environment `production` with URL `http://77.237.236.234/`.

- [ ] **Step 1: Append to `.github/workflows/ci-cd.yml` `jobs:`.**

```yaml
  image:
    name: Build and push image
    needs: [checks, terraform, compose]
    if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: read
      packages: write
    outputs:
      image: ${{ steps.tag.outputs.image }}
    steps:
      - uses: actions/checkout@v7
      - id: tag
        run: echo "image=${IMAGE}:${GITHUB_SHA}" >> "$GITHUB_OUTPUT"
      # The gha cache export needs buildx's docker-container driver.
      - uses: docker/setup-buildx-action@v4
      - uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: ${{ steps.tag.outputs.image }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    name: Deploy to production
    needs: image
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment:
      name: production
      url: http://${{ vars.VPS_DMC268_IP_T3 }}/
    # Never cancel a running apply: it would leave containers half-replaced.
    concurrency:
      group: deploy-production
      cancel-in-progress: false
    permissions:
      contents: read
      packages: read
    env:
      DEPLOY_HOST: ${{ vars.VPS_DMC268_IP_T3 }}
      APP_DIR: /opt/dmc268
      IMAGE_REF: ${{ needs.image.outputs.image }}
    steps:
      - uses: actions/checkout@v7

      - name: Configure SSH
        env:
          DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}
        run: |
          set -euo pipefail
          install -d -m 700 ~/.ssh
          install -m 600 .github/ssh/known_hosts ~/.ssh/known_hosts
          printf '%s\n' "$DEPLOY_SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          cat > ~/.ssh/config <<EOF
          Host deploy-target
            HostName ${DEPLOY_HOST}
            User deploy
            IdentityFile ~/.ssh/deploy_key
            IdentitiesOnly yes
            StrictHostKeyChecking yes
            BatchMode yes
          EOF

      - name: Upload Terraform config
        run: |
          set -euo pipefail
          # Drop old *.tf first so a file deleted from the repo is deleted on the server too.
          ssh deploy-target "install -d -m 700 $APP_DIR/infra && find $APP_DIR/infra -maxdepth 1 -name '*.tf' -delete"
          # Tracked files only (keeps exec bits); server-side state and tfvars are left alone.
          git archive --format=tar HEAD:infra | ssh deploy-target "tar -C $APP_DIR/infra -xf -"

      # Secrets travel over stdin into a 0600 file, never as arguments.
      - name: Write runtime config and secrets
        env:
          POSTGRES_PASSWORD: ${{ secrets.POSTGRES_PASSWORD }}
          EUROROUTER_API_KEYS: ${{ secrets.AI_DMC268_T3 }}
          EUROROUTER_BASE_URL: ${{ vars.AI_DMC268_URL }}
        run: |
          set -euo pipefail
          : "${POSTGRES_PASSWORD:?repo secret POSTGRES_PASSWORD is not set}"
          jq -n \
            --arg api_image "$IMAGE_REF" \
            --arg postgres_password "$POSTGRES_PASSWORD" \
            --arg eurorouter_api_keys "$EUROROUTER_API_KEYS" \
            --arg eurorouter_base_url "$EUROROUTER_BASE_URL" \
            '{api_image: $api_image, postgres_password: $postgres_password,
              eurorouter_api_keys: $eurorouter_api_keys, eurorouter_base_url: $eurorouter_base_url}' \
            | ssh deploy-target "umask 077 && cat > $APP_DIR/infra/api.auto.tfvars.json.new && mv $APP_DIR/infra/api.auto.tfvars.json.new $APP_DIR/infra/api.auto.tfvars.json"

      # A throwaway DOCKER_CONFIG keeps the short-lived token off the server's disk.
      - name: Pull image on the server
        env:
          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GHCR_USER: ${{ github.actor }}
        run: |
          set -euo pipefail
          printf '%s' "$GHCR_TOKEN" | ssh deploy-target "set -e
            export DOCKER_CONFIG=\$(mktemp -d)
            trap 'rm -rf \"\$DOCKER_CONFIG\"' EXIT
            docker login ghcr.io -u '$GHCR_USER' --password-stdin >/dev/null
            docker pull -q '$IMAGE_REF'"

      # deploy.sh: flock (shared with the frontend repo's deploy), migrations first, then the rest.
      - name: terraform apply
        run: ssh deploy-target "$APP_DIR/infra/deploy.sh"

      - name: Smoke test
        run: |
          set -euo pipefail
          curl -fsS --retry 20 --retry-delay 3 --retry-all-errors "http://${DEPLOY_HOST}/api/healthcheck"
          curl -fsS -o /dev/null "http://${DEPLOY_HOST}/"
          ssh deploy-target "docker exec dmc268-api python -m app.worker.smoke"

      # Hygiene only: in-use images are never pruned, and a week of old tags stays for rollback.
      - name: Prune old images
        continue-on-error: true
        run: ssh deploy-target "docker image prune -af --filter until=168h"
```

- [ ] **Step 2: Lint the workflow.** Run:

```bash
uv run pre-commit run check-yaml --all-files
docker run --rm -e SHELLCHECK_OPTS='-e SC2029' -v "$PWD":/repo -w /repo rhysd/actionlint:latest -color
```

Expected: `Passed`; actionlint prints nothing (exit 0). (SC2029 is excluded on purpose: `$APP_DIR`/`$IMAGE_REF` in `ssh "..."` are meant to expand on the runner.)

- [ ] **Step 3: Add a `## Deployment` section to `README.md`** (before `## Health check`):

````markdown
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
````

- [ ] **Step 4: Commit and (USER go-ahead) push.**

```bash
git add .github/workflows/ci-cd.yml README.md
git commit -m "ci: build image and deploy to production on main"
git push
```

Expected on the branch run: `checks`, `terraform`, `compose` green; `image` and `deploy` **skipped** (not `main`).

### Task 11: Pull request, merge, first production deploy

**Files:** none.

**Interfaces:** Produces a live backend at `http://77.237.236.234/api/healthcheck` — prerequisite for Task 13.

- [ ] **Step 1 (USER go-ahead): open the PR.**

```bash
gh pr create -R larchanka-training/dmc-268-api-t3 --base main --head feature/devops-cd \
  --title "Sprint 2 DevOps: Redis worker, tooling gaps, Terraform, CD to VPS" \
  --body-file - <<'EOF'
## Summary
- Redis + RQ worker (`python -m app.worker`, placeholder `ping` job; real review jobs stay in #13) and `docker compose up` now starts postgres, redis, migrate, api, worker.
- Sprint-1 gaps: strict mypy (`mypy .` clean), pre-commit hooks (auto-fix ruff/format/whitespace + mypy + pylint), DB credentials URL-escaped.
- `infra/`: Terraform (Docker provider) for postgres, redis, migrate, api, worker, web, caddy — applied on the VPS.
- CI/CD: checks on every PR; on push to `main` → image to GHCR → deploy to http://77.237.236.234/ with smoke tests (API, frontend, worker).
- Secrets: GitHub Encrypted Secrets → 0600 tfvars on the server → container env.

## Needs team-lead attention
- **Queue decision changed: RabbitMQ → Redis/RQ.** `SYSTEM_DESIGN.md` §6 and `docs/QA/TEST_PLAN.md` updated accordingly.

## Test plan
- [x] `uv run pre-commit run --all-files`, `uv run pytest`
- [x] `docker compose up --wait` + worker smoke (also with a password containing `@` and `%`)
- [x] Terraform rehearsal on local Docker: apply, re-apply keeps DB data, failed migration leaves old API running
- [x] Bootstrap workflow: `deploy` user reachable by key
- [ ] After merge: deploy job green, `http://77.237.236.234/api/healthcheck` → 200

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 2: Wait for green checks**, then (USER) merge via GitHub (squash, like PRs #10/#11).

- [ ] **Step 3: Watch the production deploy.**

```bash
gh run watch -R larchanka-training/dmc-268-api-t3 "$(gh run list -R larchanka-training/dmc-268-api-t3 -w 'CI/CD' -b main -L 1 --json databaseId --jq '.[0].databaseId')"
curl -s -w ' %{http_code}\n' http://77.237.236.234/api/healthcheck
curl -s -o /dev/null -w 'root %{http_code}\n' http://77.237.236.234/
```

Expected: all five jobs green; `{"status":"ok"} 200`; `root 200` (nginx placeholder until Task 13). If the first `curl` from the runner times out but `ssh deploy@… curl -s localhost/api/healthcheck` works, port 80 is blocked by the provider's firewall — tell the user (Contabo panel), do not open other ports.

---

## Part B — Frontend repo (`dmc-268-ui-t3`)

All Part B paths are relative to `/Users/bogdanyakovenko/IdeaProjects/larchanka-training/review-bot-main/dmc-268-ui-t3`. Use `npx -y pnpm@12.9.1 …` wherever this plan says `pnpm …` if the global pnpm is not 12.x (the laptop has 9.15.9; hooks also work with it — verified).

### Task 12: Frontend tooling (sprint-1 frontend task 1)

**Files:** Modify `package.json`, `tsconfig.json`, `src/App.tsx`, `src/main.tsx`, `README.md`; create `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `.nvmrc`, `eslint.config.js`, `.prettierrc.json`, `.prettierignore`, `stylelint.config.js`, `.husky/pre-commit`, `.husky/pre-push`.

**Interfaces:** Produces scripts `pnpm check-types`, `pnpm lint`, `pnpm format:check`, `pnpm build` (all exit 0) — Task 13's CI calls exactly these. `packageManager: pnpm@12.9.1`, `.nvmrc` `24`.

- [ ] **Step 1: Clone and branch.**

```bash
cd /Users/bogdanyakovenko/IdeaProjects/larchanka-training/review-bot-main
git clone https://github.com/larchanka-training/dmc-268-ui-t3.git
cd dmc-268-ui-t3
git switch -c feature/frontend-tooling
```

(HTTPS: SSH clone fails with `Permission denied (publickey)` on this laptop.)

- [ ] **Step 2: Package manager and Node.** Create `.nvmrc` containing `24`. Create `pnpm-workspace.yaml`:

```yaml
# "packages" keeps older global pnpm (9.x) happy; allowBuilds is pnpm 10+'s opt-in for install scripts.
packages:
  - '.'
allowBuilds:
  esbuild: true
```

- [ ] **Step 3: Scripts + lint-staged.** Edit `package.json` so it contains (keep existing `name`, `private`, `version`, `type`, `dependencies`):

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "check-types": "tsc --noEmit",
    "lint": "eslint . --max-warnings=0 && stylelint \"src/**/*.css\" --allow-empty-input",
    "lint:fix": "eslint . --fix && stylelint \"src/**/*.css\" --allow-empty-input --fix",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "prepare": "husky || true"
  },
  "packageManager": "pnpm@12.9.1",
  "engines": {
    "node": ">=22.22.1"
  },
  "lint-staged": {
    "*.{ts,tsx}": ["eslint --fix --max-warnings=0", "prettier --write"],
    "*.css": ["stylelint --fix", "prettier --write"],
    "*.{json,md,html,yml,yaml}": ["prettier --write"]
  }
```

(`husky || true`: the Docker build in Task 13 has no `.git`; `tsc --noEmit` because `tsconfig.json` already has `noEmit: true`.)

- [ ] **Step 4: Install dev dependencies.**

```bash
npx -y pnpm@12.9.1 add -D eslint@^10.12.0 @eslint/js@^10.0.1 typescript-eslint@^8.71.1 \
  eslint-plugin-react-hooks@^7.1.1 eslint-plugin-react-refresh@^0.5.7 eslint-config-prettier@^10.1.8 \
  globals@^17.13.0 prettier@^3.9.9 stylelint@^17.16.0 stylelint-config-standard@^40.0.0 \
  husky@^9.1.7 lint-staged@^17.6.0
```

Expected: ends with `Done … using pnpm v12.9.1`; `pnpm-lock.yaml` created; `prepare$ husky` ran (creates `.husky/_`, ignored by its own `.gitignore`).

- [ ] **Step 5: Config files.**

`eslint.config.js`:

```js
import js from '@eslint/js'
import prettierConfig from 'eslint-config-prettier'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', 'coverage']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      reactHooks.configs.flat['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  // Last: turns off stylistic rules that would fight Prettier.
  prettierConfig,
])
```

`.prettierrc.json`:

```json
{
  "semi": false,
  "singleQuote": true,
  "trailingComma": "all",
  "printWidth": 100
}
```

`.prettierignore`:

```
dist
node_modules
pnpm-lock.yaml
coverage
```

`stylelint.config.js`:

```js
export default {
  extends: ['stylelint-config-standard'],
}
```

In `tsconfig.json` change `"include": ["src"]` to `"include": ["src", "vite.config.ts"]` (typed linting needs every linted `.ts` file in a project).

- [ ] **Step 6: Run lint to see the current violations.**

Run: `npx -y pnpm@12.9.1 lint`
Expected: FAIL with exactly two errors — `src/App.tsx` `@typescript-eslint/no-confusing-void-expression` and `src/main.tsx` `@typescript-eslint/no-non-null-assertion`.

- [ ] **Step 7: Fix them.** `src/App.tsx` — replace the button with:

```tsx
      <button
        onClick={() => {
          setCount((count) => count + 1)
        }}
      >
        Count is {count}
      </button>
```

`src/main.tsx` — replace the `ReactDOM.createRoot(document.getElementById('root')!)` call with:

```tsx
const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element #root not found in index.html')
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 8: Format everything once, then run the DoD commands.**

```bash
p() { npx -y pnpm@12.9.1 "$@"; }   # a function, not P="...": zsh doesn't word-split $P
p format
p lint && p check-types && p format:check && p build
```

Expected: `lint` no output beyond the command echo; `check-types` clean; `All matched files use Prettier code style!`; `✓ built in …`. Also prove Stylelint is wired: `printf 'a { colr: red; }\n' > src/bad.css && p lint; echo "exit=$?"; rm src/bad.css` → `exit=2` with `property-no-unknown`.

- [ ] **Step 9: Husky hooks.** `.husky/pre-commit`:

```sh
pnpm exec lint-staged
```

`.husky/pre-push`:

```sh
pnpm check-types
pnpm lint
```

- [ ] **Step 10: Prove Husky blocks a bad commit (DoD).**

```bash
printf 'export const x: any = 1\n' > src/bad.ts
git add src/bad.ts
git commit -m "should be blocked"; echo "commit exit=$?"
git reset -q HEAD src/bad.ts && rm src/bad.ts
```

Expected: `@typescript-eslint/no-explicit-any` error, `husky - pre-commit script failed (code 1)`, `commit exit=1`; `git log -1` unchanged.

- [ ] **Step 11: README.** Replace the `## Setup & Run` section with:

````markdown
## Setup & Run

Requires Node 24 (`.nvmrc`) and pnpm 12 (`npm install -g pnpm@12.9.1`, or `corepack enable`).

```bash
pnpm install      # also installs the Husky git hooks
pnpm dev
```

## Quality checks

| Command | What it does |
|---|---|
| `pnpm lint` | ESLint (typescript-eslint strict, type-aware) + Stylelint, zero warnings allowed |
| `pnpm lint:fix` | same, auto-fixing what it can |
| `pnpm check-types` | `tsc --noEmit` (strict) |
| `pnpm format` / `pnpm format:check` | Prettier |
| `pnpm build` | type-check + production build into `dist/` |

Git hooks (Husky): **pre-commit** runs lint-staged (ESLint/Stylelint `--fix` + Prettier on staged files) and blocks the commit on errors; **pre-push** runs `check-types` and `lint`.
````

- [ ] **Step 12: Commit** (goes through the new hook), **push and PR (USER go-ahead).**

```bash
git add -A
git status --short   # expect only the files listed in this task + pnpm-lock.yaml + Prettier-reformatted files
git commit -m "chore: add pnpm, ESLint, Prettier, Stylelint and Husky"
git push -u origin feature/frontend-tooling
gh pr create -R larchanka-training/dmc-268-ui-t3 --base main --head feature/frontend-tooling \
  --title "Frontend tooling: pnpm, ESLint, Prettier, Stylelint, Husky" \
  --body "Closes the sprint-1 frontend tooling task: \`pnpm lint\`, \`pnpm check-types\`, \`pnpm build\` pass; Husky pre-commit (lint-staged) blocks commits with lint errors; pre-push runs types + lint.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

This PR has no CI yet (that arrives in Task 13) and can merge independently of the backend work.

### Task 13: Frontend image + CD

**Files:** Create `Dockerfile`, `.dockerignore`, `docker/nginx.conf`, `.github/ssh/known_hosts`, `.github/workflows/ci-cd.yml`.

**Interfaces:**
- Consumes: Task 12 scripts; Task 10's server layout (`/opt/dmc268/infra`, `api.auto.tfvars.json`, `/opt/dmc268/infra/deploy.sh`, `web_image` variable, web container on port 8080); secret `DEPLOY_SSH_KEY` (set in Task 9 Step 1); var `VPS_DMC268_IP_T3`.
- Produces: `ghcr.io/larchanka-training/dmc-268-ui-t3:<sha>` served at `http://77.237.236.234/`.

- [ ] **Step 1: Branch from the tooling branch** (or from `main` once Task 12 is merged):

```bash
git switch -c feature/frontend-cd
```

- [ ] **Step 2: Image files.** `docker/nginx.conf`:

```nginx
server {
    listen 8080;
    root /usr/share/nginx/html;

    # Hashed Vite assets never change; index.html must always be revalidated.
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # SPA fallback: unknown paths get index.html and the client router takes over.
    location / {
        add_header Cache-Control "no-cache";
        try_files $uri $uri/ /index.html;
    }
}
```

`Dockerfile`:

```dockerfile
FROM node:24-alpine AS build
RUN npm install -g pnpm@12.9.1
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# Unprivileged nginx listens on 8080, which the backend repo's Caddy proxies to.
FROM nginxinc/nginx-unprivileged:1.30-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
```

`.dockerignore`:

```
node_modules
dist
.git
.husky
```

- [ ] **Step 3: Verify the image locally.**

```bash
docker build -t dmc268-web:local .
docker run -d --rm --name web-check -p 18081:8080 dmc268-web:local
curl -s http://localhost:18081/ | grep -o '<title>.*</title>'
curl -s http://localhost:18081/some/client/route | grep -o '<title>.*</title>'
curl -sI http://localhost:18081/ | grep -i cache-control
docker stop web-check && docker image rm dmc268-web:local
```

Expected: `<title>DMC-268 UI (Team 3)</title>` twice; `Cache-Control: no-cache`.

- [ ] **Step 4: Pin host keys.** Create `.github/ssh/known_hosts` with the same three lines as the backend repo's `.github/ssh/known_hosts` (Task 9 Step 2).

- [ ] **Step 5: Create `.github/workflows/ci-cd.yml`.**

```yaml
name: CI/CD

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

env:
  IMAGE: ghcr.io/${{ github.repository }}

jobs:
  checks:
    name: Types, lint, format, build
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7
      # pnpm version comes from package.json "packageManager".
      - uses: pnpm/action-setup@v6
      - uses: actions/setup-node@v7
        with:
          node-version-file: .nvmrc
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm check-types
      - run: pnpm lint
      - run: pnpm format:check
      - run: pnpm build

  image:
    name: Build and push image
    needs: checks
    if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
      packages: write
    outputs:
      image: ${{ steps.tag.outputs.image }}
    steps:
      - uses: actions/checkout@v7
      - id: tag
        run: echo "image=${IMAGE}:${GITHUB_SHA}" >> "$GITHUB_OUTPUT"
      - uses: docker/setup-buildx-action@v4
      - uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: ${{ steps.tag.outputs.image }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    name: Deploy to production
    needs: image
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment:
      name: production
      url: http://${{ vars.VPS_DMC268_IP_T3 }}/
    concurrency:
      group: deploy-production
      cancel-in-progress: false
    permissions:
      contents: read
      packages: read
    env:
      DEPLOY_HOST: ${{ vars.VPS_DMC268_IP_T3 }}
      APP_DIR: /opt/dmc268
      IMAGE_REF: ${{ needs.image.outputs.image }}
    steps:
      - uses: actions/checkout@v7

      - name: Configure SSH
        env:
          DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}
        run: |
          set -euo pipefail
          install -d -m 700 ~/.ssh
          install -m 600 .github/ssh/known_hosts ~/.ssh/known_hosts
          printf '%s\n' "$DEPLOY_SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          cat > ~/.ssh/config <<EOF
          Host deploy-target
            HostName ${DEPLOY_HOST}
            User deploy
            IdentityFile ~/.ssh/deploy_key
            IdentitiesOnly yes
            StrictHostKeyChecking yes
            BatchMode yes
          EOF

      # The Terraform config and backend secrets are owned by dmc-268-api-t3's deploy.
      - name: Check the backend stack exists
        run: |
          ssh deploy-target "test -f $APP_DIR/infra/api.auto.tfvars.json" || {
            echo "::error::Backend infra is not deployed on the server yet. Deploy dmc-268-api-t3 (push to its main) first."
            exit 1
          }

      - name: Point Terraform at the new web image
        run: |
          set -euo pipefail
          jq -n --arg web_image "$IMAGE_REF" '{web_image: $web_image}' \
            | ssh deploy-target "umask 077 && cat > $APP_DIR/infra/web.auto.tfvars.json.new && mv $APP_DIR/infra/web.auto.tfvars.json.new $APP_DIR/infra/web.auto.tfvars.json"

      - name: Pull image on the server
        env:
          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GHCR_USER: ${{ github.actor }}
        run: |
          set -euo pipefail
          printf '%s' "$GHCR_TOKEN" | ssh deploy-target "set -e
            export DOCKER_CONFIG=\$(mktemp -d)
            trap 'rm -rf \"\$DOCKER_CONFIG\"' EXIT
            docker login ghcr.io -u '$GHCR_USER' --password-stdin >/dev/null
            docker pull -q '$IMAGE_REF'"

      # Same entrypoint the backend deploy uses (lock + migrations-first apply).
      - name: terraform apply
        run: ssh deploy-target "$APP_DIR/infra/deploy.sh"

      - name: Smoke test
        run: |
          set -euo pipefail
          curl -fsS --retry 20 --retry-delay 3 --retry-all-errors "http://${DEPLOY_HOST}/" | grep -q '<title>DMC-268 UI (Team 3)</title>'
          curl -fsS "http://${DEPLOY_HOST}/some/client/route" | grep -q '<title>DMC-268 UI (Team 3)</title>'
          curl -fsS "http://${DEPLOY_HOST}/api/healthcheck"

      - name: Prune old images
        continue-on-error: true
        run: ssh deploy-target "docker image prune -af --filter until=168h"
```

- [ ] **Step 6: Lint workflow + formatting.**

```bash
docker run --rm -e SHELLCHECK_OPTS='-e SC2029' -v "$PWD":/repo -w /repo rhysd/actionlint:latest -color
npx -y pnpm@12.9.1 format:check
```

Expected: actionlint silent; Prettier clean (run `pnpm format` first if the new YAML needs reformatting).

- [ ] **Step 7: Commit, push, PR (USER go-ahead).**

```bash
git add Dockerfile .dockerignore docker/nginx.conf .github
git commit -m "ci: build static image and deploy to production on main"
git push -u origin feature/frontend-cd
gh pr create -R larchanka-training/dmc-268-ui-t3 --base main --head feature/frontend-cd \
  --title "Frontend CD: nginx image to GHCR, deploy to VPS on main" \
  --body "Adds CI (types, lint, format, build) on every PR and, on push to \`main\`, builds \`ghcr.io/larchanka-training/dmc-268-ui-t3:<sha>\` and deploys it via the backend repo's Terraform on the VPS (http://77.237.236.234/). Merge after dmc-268-api-t3's DevOps PR has deployed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: PR `checks` job green; `image`/`deploy` skipped.

### Task 14: End-to-end DoD verification

**Files:** none in the repos; Obsidian notes.

- [ ] **Step 1 (USER): merge `feature/frontend-tooling` then `feature/frontend-cd`** (after Task 11's deploy is green). Watch the UI repo's `CI/CD` run on `main` — all three jobs green.

- [ ] **Step 2: Verify the DoD from the laptop.**

```bash
curl -s http://77.237.236.234/ | grep -o '<title>.*</title>'
curl -s -w ' %{http_code}\n' http://77.237.236.234/api/healthcheck
curl -s -o /dev/null -w 'docs %{http_code}\n' http://77.237.236.234/api/docs
ssh -i ~/.ssh/dmc268_t3_deploy deploy@77.237.236.234 'docker ps --format "{{.Names}} {{.Status}}" && docker exec dmc268-api python -m app.worker.smoke && echo worker-ok'
nc -z -G 4 77.237.236.234 5432 && echo "5432 OPEN (bad)" || echo "5432 closed (good)"
nc -z -G 4 77.237.236.234 6379 && echo "6379 OPEN (bad)" || echo "6379 closed (good)"
```

Expected: `<title>DMC-268 UI (Team 3)</title>`; `{"status":"ok"} 200`; `docs 200`; seven `dmc268-*` containers `Up` except `dmc268-migrate` (not listed, it exited); `worker-ok`; both ports closed.

- [ ] **Step 3: Prove "deploys automatically on merge".** Make a trivial docs-only PR in the backend repo (e.g. fix a README typo), merge it, and confirm a new `CI/CD` run on `main` ends with `deploy` green and `docker inspect -f '{{.Config.Image}}' dmc268-api` (over ssh) shows the new commit sha.

- [ ] **Step 4: Update Obsidian.** Run the obsidian-docs update workflow for `dmc-268-api-t3`: append a dated "deployed" section to `~/Obsidian/dmc-268-api-t3/devops.md` (live URL, workflows, secrets names, gotchas met during rollout) and restamp `index.md` `last_synced_commit` with the new `main` HEAD.

---

## Execution notes (2026-10-07) — where the implementation differs from the text above

Applied after the final whole-branch review; the code, not the snippets above, is authoritative.

- **Deploys are staged, not written in place.** Jobs upload to `/opt/dmc268/incoming/<repo>-<run id>/` and run `infra/deploy.sh <stage>`; the script takes `flock /opt/dmc268/deploy.lock`, promotes the files into `/opt/dmc268/infra`, applies (migrations first), deletes the stage on success and **restores the previous `*.auto.tfvars.json` on failure**. The frontend job stages only `web.auto.tfvars.json`. Reason: the first design changed live files outside the lock (races between the two repos, possible apply with a half-replaced config).
- Images are pulled before staging; `docker_image` resources then find them locally.
- Each deploy job skips itself when its commit is no longer the tip of `main`; on failure it prints `docker logs dmc268-migrate`.
- `docker_volume.postgres_data` has `prevent_destroy = true`.
- `database_url` uses `quote(..., safe="")` (not `quote_plus`, which breaks passwords containing a space).
- `bootstrap.yml` is manual (`workflow_dispatch`) only; password auth allows `keyboard-interactive`.
- Image pruning only removes old `ghcr.io/*` images (Docker Hub images stay cached).
