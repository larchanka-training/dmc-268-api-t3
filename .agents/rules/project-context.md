# Project context and decision sources

## Architecture baseline

This package follows [System Design v1, revision a9277b9](https://github.com/larchanka-training/dmc-268-api-t3/blob/a9277b9da2b6b066dbe889a6d5fdd1b9436cf74f/SYSTEM_DESIGN.md), dated September 14, 2026. The document is a draft for team review. The user requested alignment with this revision; that does not establish team approval or resolve its open decisions. Use this pinned reference from either repository and record a replacement revision when the team adopts changes.

Read approved task/API/event contracts and current repository instructions alongside this baseline. If they conflict, identify the exact decision and its owner; do independent work without silently choosing an incompatible contract. Do not treat an absent implementation as a change to the architectural standard.

## Team standards and v1 decisions

- Python/FastAPI/SQLAlchemy/PostgreSQL with uv, Ruff, and Pylint; TypeScript/React/Node.js with pnpm, ESLint, Zod, Zustand, and Vitest. Use manifest/lockfile versions and configured commands. Use Skylos in the team's configured language scope.
- RabbitMQ carries asynchronous work; PostgreSQL is the source of truth. Redis is excluded from v1. A future cache or distributed limiter may use it behind abstractions after a separate decision.
- VcsReader and VcsPublisher isolate GitHub/GitLab reads and summary publication. Logical components do not require independent microservices. The runtime reviewer does not execute reviewed code, modify it, or perform commit/push/merge operations; inline comments and repository-wide indexing are outside v1.
- JWT authentication, resource authorization, rate limiting, and controlled network boundaries follow accepted security contracts. Credentials never enter queue messages, prepared model context, or LLM requests.
- The sprint named Ollama SDK; the design leaves the provider/model open. Keep an approved Ollama adapter behind LLM Gateway, but do not bind the domain to it or select a new provider without the owning decision.
- Sprint review languages were Go, Java, JavaScript, Python, and PHP; the team's TypeScript code also needs review. The design leaves supported languages/categories open. Pass the task's approved language set and immutable enabled rules to the reviewer; the historical list is not proof of parser or model support.

## Sprint coding and ownership rules

Source: “Sprint 1 - 2026_09_04 17_48 CEST - Notes by Gemini”, September 4, 2026. The five-argument convention, security priority, and human review complement the architecture.

Limit functions to five explicit parameters, excluding Python self/cls. Prefer cohesive typed inputs, not arbitrary dictionaries or variadic arguments hiding an oversized API. Preserve mandatory framework/interface signatures and document justified exceptions.

Prioritize evidence-backed security findings while applying the supplied enabled RuleSet. The meeting names Alexander as required approver; resolve the actual account/team from repository ownership settings rather than inventing a username. Human approval of changes is separate from automated runtime review. Machine-generated ambiguities such as “PNPM for Python” do not define new tools.

## Open decisions

The team owns supported languages/categories, rule catalog and inheritance, provider/model and retention, webhook event mapping, context budgets, matching thresholds, retry counts/backoff, stale-RUNNING recovery, concurrency, deployment and security scopes, and numerical NFRs. The design's sample limits are not commitments. In particular, do not infer FIXED findings from partial analysis; the matching owner must define baseline eligibility and sufficient absence evidence.

The [review contract](review-contract.md) defines local candidate-format and validation conventions needed for reproducible templates. It does not declare a database/API schema approved by the team.

## Delivery scope

The deliverable is `.agents`: development/testing/review skills, prompts, rules, and local templates. Application and infrastructure owners implement the services. The tech lead owns root AGENTS.md; do not create it as part of this package. Write Markdown in English except README.md in Russian. Follow the current delivery request; local-only work means no staging, commits, pushes, or remote edits.
