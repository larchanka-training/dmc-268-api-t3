## 1. Context Level 1

- [x] 1.1 Define typed context schemas and parse unified diff files, hunks, and line numbers; verify additions, deletions, multiple hunks, empty input, and malformed ranges.
- [x] 1.2 Support renames, quoted paths, newline markers, and excluded binary/lock/minified files; verify parser fixtures and rejection of unsupported combined patches.

## 2. VCS adapters

- [x] 2.1 Implement GitHub metadata/raw-diff retrieval with trusted origins, typed errors, limits, and revision checks; verify HTTP contract tests.
- [x] 2.2 Implement GitLab metadata/raw-diff retrieval with completeness and revision checks; verify HTTP contract tests including pagination/overflow behavior where applicable.

## 3. Durable ingestion

- [x] 3.1 Add structured-context and delivery/revision persistence migrations; verify upgrade/downgrade and database uniqueness.
- [x] 3.2 Implement transactional ingestion and duplicate delivery/source-event handling; verify sequential and concurrent PostgreSQL tests.
- [x] 3.3 Add RQ ingestion and recovery dispatch with bounded retries; verify queue outages and crash-window recovery.

## 4. Webhook transport

- [x] 4.1 Add provider authentication and payload normalization; verify invalid signatures, GitLab freshness/token modes, and supported/ignored events.
- [x] 4.2 Connect webhook endpoints to durable acceptance; verify response semantics, repeated delivery, and persisted parsed context through API tests.

## 5. Acceptance and documentation

- [x] 5.1 Add signed replay script and CI smoke coverage; verify backend-to-context flow and negative authentication cases.
- [x] 5.2 Add the optional ngrok tunnel as an opt-in `tunnel` docker compose profile and a host command, with local-only `NGROK_DOMAIN`/`NGROK_AUTHTOKEN` configuration and WORKFLOW instructions; verify `docker compose config` with and without the profile.
- [x] 5.3 Document bot account setup, token permissions, configuration, and architecture; verify instructions against implemented contracts and report live-check availability.
- [ ] 5.4 Run required quality/integration checks, reconcile artifacts, and archive the completed change only when all requirements are satisfied.
