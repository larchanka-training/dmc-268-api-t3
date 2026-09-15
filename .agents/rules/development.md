# Development rules

## Sources of decisions

Read the tech lead's current instructions, the [project context](project-context.md), working branch configuration, relevant tests, and task contract. Preserve established architectural boundaries and the style of neighboring code. If the implementation differs from the target stack, state the difference explicitly; do not migrate the project as part of an unrelated task.

Do not create `AGENTS.md` for this package. Do not present an assumption as an approved ADR. A contract change must account for callers, errors, serialization, and tests on both sides.

Write Markdown files in English, except files named `README.md`, which must be written in Russian. Apply this convention to skill descriptions, rules, prompts, and templates as well as their examples.

## Stack and boundaries

- Frontend: strict TypeScript, React, Vite/Node.js, pnpm, ESLint, Zod for external data, Zustand for shared client state, and Vitest for tests. Take the Node version from project/CI configuration; report it as missing if none is specified.
- Backend: Python, FastAPI, SQLAlchemy, PostgreSQL, and a model adapter behind LLM Gateway (the sprint named Ollama SDK); uv for dependencies, Ruff for style, and Pylint for analysis. Obtain the Python version and formatter constraints from pyproject rather than guessing.
- Skylos complements the configured language tooling. Use the version, supported language scope, and invocation maintained by the team; it does not replace ESLint/Ruff.
- RabbitMQ handles work delivery; PostgreSQL holds durable state. Redis is excluded from v1.

## Implementation

Limit functions to five arguments as specified in [project context](project-context.md). Prefer typed request/value objects for cohesive input. Keep mandatory framework signatures compatible and record justified exceptions. This maintainability rule does not by itself prove or prevent a security vulnerability.

Separate transport, application use cases, and infrastructure adapters where those boundaries already exist. Do not build a generic framework for one endpoint. Validate external DTOs at the boundary and use typed domain values internally. Return predictable errors that distinguish invalid input, missing permissions, and transient dependency failures.

Do not log secrets, PR contents, JWTs, or complete model responses. Use `run_id`, stage, error identifier, and safe counters for diagnostics. Do not disable linters globally to silence one warning.

SQLAlchemy: one session per request/unit of work, explicit transaction boundaries, rollback on failure, and session cleanup. Do not share a session between parallel tasks. Synchronous database/SDK calls must not block the event loop; choose a synchronous endpoint/thread pool or a fully asynchronous adapter consistently. Network operations need timeouts and bounded retries for allowed failures only.

Frontend: validate JSON with Zod before storing it; `as SomeDto` does not validate a response. Use selectors and minimal shared state. An old response must not overwrite the user's newly selected PR/run; handle cancellation and effect cleanup. Show loading, empty, error, partial, and stale states where relevant. Do not put JWTs in URLs, logs, or arbitrarily chosen persistent storage; follow the accepted authentication design.

## Verification and delivery

Check the manifest and lockfile first. `uv sync --locked` and `pnpm install --frozen-lockfile` require an existing, current lockfile. Do not claim a test passed when its command is unavailable, its environment is not ready, or only a static inspection was performed.

For a bug fix, add a test of observable behavior. For templates and prompts, check actual output examples and rejection of invalid results. Do not execute untrusted PR code as part of the AI review service. Tests of your own development run separately in an authorized development environment.

Commit format: `<ISSUEID>: <exact issue title>`, for example `42: Prepare the backend skill`. Keep one logical result per commit and exclude unrelated changes. A PR lists linked issues, behavior, actual verification results, and remaining limitations. Sending work for review does not imply merging or closing an issue; mark Done after team acceptance.

## Review checklist

| Rule | Failure without it | Verification |
| --- | --- | --- |
| Fixed SHA | A report mixes two versions of a file | Verify that a push during analysis cannot alter the report snapshot |
| Session per unit of work | Parallel requests mix transactions | Identify begin/commit/rollback boundaries |
| Explicit timeout and retry budget | A request hangs forever or overloads VCS | Trace failure after the last attempt |
| Validated DTO at the boundary | Invalid data spreads through the application | Construct JSON with an incorrect type |
| Actual verification results | The team accepts unverified code | Reproduce one reported command |

Use the [team workflow](../workflows.md). Human review and ownership follow [project context](project-context.md).
