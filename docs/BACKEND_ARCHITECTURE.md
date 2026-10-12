# Backend Architecture

## Layering (Clean/Hexagonal)

```
Router (FastAPI) -> Service (business logic) -> Repository (data access) -> LLM Gateway (external LLM calls)
                                                        |
                                                   PostgreSQL
```

- **Router** — FastAPI path operation functions. Parse/validate HTTP input, call a Service, return a response model. No business logic, no direct DB or LLM access.
- **Service** — orchestrates a use case (e.g. "review a merge request"). Calls one or more Repositories and the LLM Gateway. No FastAPI or SQLAlchemy imports.
- **Repository** — one class per aggregate (`RepositoryRepo`, `MergeRequestRepo`, `ReviewJobRepo`, `FindingRepo`), wrapping SQLAlchemy `Session` queries against the models in `app/models/`. No business logic.
- **LLM Gateway** — the `app/llm/` package (separate PR): abstracts over LLM providers, returns validated `ReviewResult` Pydantic objects. Services depend on the Gateway's interface, never on a concrete provider.

Only the data layer (models + migration) is implemented in this PR. Router/Service/Repository classes are documented here as the target shape for the next PR that adds business endpoints.

## Data Model

See `docs/architecture/erd.md` for the ER diagram. Entities, in `app/models/`:

| Entity | Table | Purpose |
|---|---|---|
| `Repository` | `repositories` | A tracked Git repository (e.g. a GitLab/GitHub project). |
| `MergeRequest` | `merge_requests` | One MR/PR opened against a `Repository`. |
| `ReviewJob` | `review_jobs` | One review run for a `MergeRequest`, with a `status` state machine (`pending` -> `running` -> `completed`/`failed`). |
| `ContextPayload` | `context_payloads` | The diff + metadata assembled for exactly one `ReviewJob` (1:1). |
| `Finding` | `findings` | One review comment/finding produced for a `ReviewJob` (1:many). |

## Migrations

Alembic (`alembic/`), configured to read the DB URL from `app.config.settings.database_url`. Run `uv run alembic upgrade head` to apply.

## Queue and worker

Background work goes through Redis with [RQ](https://python-rq.org/):

- `app/queue.py` — `get_redis()` / `get_queue()`. Code enqueues with `get_queue().enqueue(func, *args)`; the job function must live in an importable module (not `__main__`).
- `app/worker/` — the worker process, `python -m app.worker`, run from the same image as the API with a different command. `tasks.py` holds job functions; `ping` is a placeholder until review jobs land (issue #13).
- `python -m app.worker.smoke` enqueues `ping` and waits for its result — CI and the deploy pipeline use it to prove API → Redis → worker works.
