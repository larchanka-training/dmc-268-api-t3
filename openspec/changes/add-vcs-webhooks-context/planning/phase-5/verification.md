# Phase 5 verification — add-vcs-webhooks-context

Date: 2026-10-12. Scope: task 5.4 only, verification and necessary factual documentation corrections. No commits, task checkbox changes, archive, production deployment, or application code changes were performed by this implementer. Commands/checks belong to the parent. Results below are explicitly attributed to the parent; source inspection is not treated as test execution.

## Command results supplied by the parent

- `rtk proxy uv sync --locked`: **PASS**, parent-reported.
- `rtk proxy uv run pre-commit run --all-files`: **PASS**, parent-reported before this report and its documentation corrections. The parent must rerun this check on the final documentation edits.
- `rtk proxy uv run pytest` with isolated PostgreSQL 16 and Redis 7: **PASS**, 520 passed, 0 skipped. Baseline without the integration variables: 411 passed, 109 skipped. The isolated run exercised the previously skipped integration tests. Exact command environment and output remain in the parent's execution record; no credentials are reproduced here.
- `rtk proxy docker compose config --quiet`: **PASS**, parent-reported.
- `rtk proxy docker compose --profile tunnel config --quiet`: **PASS**, parent-reported.
- `rtk proxy docker compose -f docker-compose.yml -f docker-compose.smoke.yml config --quiet`: **PASS**, parent-reported.
- Isolated compose project `webhook-phase5`, API port `18000`: build / `up --wait`, healthcheck, and `python -m app.worker.smoke`: **PASS**, parent-reported. The live stack was retained.
- `scripts/smoke_webhooks.sh`, all five cases: **PASS**, parent-reported. Isolated project `down -v` completed; the live stack was untouched. This is simulated provider traffic against a stub upstream.
- Terraform `fmt -check -diff`: **PASS**, parent-reported. Initial validation lacked a provider; subsequent `init -backend=false` and `validate` both **PASS**, parent-reported. The initial failure is resolved, not an outstanding blocker.
- `rtk proxy openspec validate add-vcs-webhooks-context --strict`: **PASS**, parent-reported.
- Branch CI (`checks`, `terraform`, `compose`): **SUCCESS** for revision `03a8f1f`, parent-reported and verified through the jobs API: [Actions run 38266503594](https://github.com/larchanka-training/dmc-268-api-t3/actions/runs/38266503594). This supersedes the earlier public API lookup with no runs. [PR19](https://github.com/larchanka-training/dmc-268-api-t3/pull/19) exists. CI success applies to this revision; it does not verify the replay disclosure fix currently underway.

Branch CI evidence is now available for `03a8f1f`. Final documentation verification and the replay stdout disclosure fix/regression remain pending; this report does not claim all final checks have passed.

## Scenario-to-test traceability

All ten delta-spec scenarios have source-level test mappings below. Test identifiers are actual functions in the repository. Their passing status is supplied by the parent's full isolated run (520 passed, 0 skipped), rather than inferred from their existence. Parameterized functions represent all collected cases in that run. Smoke case numbers refer to `scripts/smoke_webhooks.sh`.

### webhook-ingestion/spec.md

1. **Authenticated delivery** — invalid authentication rejects the request without delivery or review work.
   - `tests/api/test_webhooks.py::test_github_missing_or_wrong_signature_is_401`, `::test_github_tampered_body_is_401`, `::test_gitlab_signed_mode_rejects_bad_stale_and_token_only_requests`, and `::test_gitlab_token_mode_rejects_wrong_token` cover invalid provider authentication.
   - `tests/api/test_webhooks.py::test_malformed_body_under_bad_signature_is_401_not_400` verifies authentication precedes parsing; `::test_ignored_invalid_and_unauthenticated_requests_leave_no_rows` verifies no inbox rows or queued jobs.
   - Smoke case 4 asserts 401 and zero delivery rows for unsigned/wrong-secret GitHub, stale GitLab signatures, and token-only GitLab requests to signed mode. GitLab token mode is covered in tests, not the signed-mode smoke stack.
2. **Supported source events** — authenticated unrelated events create no ingestion work.
   - `tests/api/test_webhooks.py::test_github_unrelated_authenticated_events_are_ignored`, `::test_gitlab_unrelated_authenticated_events_are_ignored`, `::test_gitlab_other_hook_type_is_ignored`, and `::test_ignored_invalid_and_unauthenticated_requests_leave_no_rows` verify ignored responses and absence of work.
   - Smoke case 5: signed GitHub ping -> ignored, zero delivery rows. Live PR18 additionally exercises the supported synchronize action; it does not prove every event-matrix entry.
3. **Durable idempotent acceptance** — concurrent duplicates create at most one job/context per revision.
   - `tests/ingestion/test_concurrency.py::test_concurrent_same_delivery_id_creates_one_row` and `::test_concurrent_same_revision_different_delivery_ids_creates_one_row` verify concurrent inbox uniqueness.
   - `tests/ingestion/test_concurrency.py::test_concurrent_workers_on_one_delivery_produce_one_job_and_context` verifies one VCS fetch, one job, and one context under concurrent worker claims.
   - `tests/api/test_webhook_ingestion_flow.py::test_redelivery_after_processing_creates_no_second_context` covers API-to-worker redelivery. Database constraints: `tests/db/test_constraints.py::test_duplicate_provider_delivery_id_rejected` and `::test_duplicate_revision_rejected`.
   - Smoke case 2 covers sequential same-id and same-revision redelivery. Live PR18 verifies one job/context after user redelivery; neither smoke nor live redelivery is a concurrency test.
4. **Dispatch recovery** — accepted work survives Redis failure and is redispatched.
   - `tests/api/test_webhooks.py::test_queue_outage_still_accepts_and_leaves_row_for_recovery` and `tests/ingestion/test_dispatch.py::test_accept_and_dispatch_keeps_the_row_when_the_queue_is_down` verify durable acceptance despite queue failure.
   - `tests/ingestion/test_dispatch.py::test_accepted_not_dispatched_is_picked_up_by_dispatcher`, `::test_dispatched_not_claimed_is_redispatched_only_when_stale_and_job_gone`, `::test_claimed_then_worker_died_is_redispatched_after_lease_expiry`, and `::test_retry_scheduled_delivery_waits_for_next_attempt_at_then_redispatches` cover recovery windows.
   - No compose smoke case deliberately interrupts Redis; this scenario relies on the integration tests. Production wiring is present, runtime readiness is separately unverified.
5. **Persisted context** — successful ingestion makes complete structured context available to its review job.
   - `tests/api/test_webhook_ingestion_flow.py::test_signed_delivery_yields_persisted_context_level_one` is parameterized for GitHub/GitLab and executes a burst RQ worker with real adapters over HTTP mocks. It checks SUCCEEDED delivery, linked job/context, raw diff, head/base SHAs, structured lines, and provider metadata.
   - `tests/ingestion/test_ingest.py::test_success_creates_job_and_context_atomically` covers atomic persistence. `tests/db/test_migration.py::test_upgrade_downgrade_upgrade_with_existing_rows` and `::test_orm_metadata_matches_migrations` cover migration reconciliation.
   - Smoke cases 1 and 3 verify succeeded delivery, expected head/base, nonempty diff, schema version, and structured files against the running stack. Live GitHub evidence is below.

### vcs-retrieval/spec.md

6. **Bounded provider retrieval** — metadata/diff come from configured origins using bounded authenticated calls.
   - `tests/vcs/test_github.py::test_fetches_metadata_diff_metadata_with_headers` and `tests/vcs/test_gitlab.py::test_fetches_metadata_diff_metadata_with_private_token` assert adapter request paths, headers, and metadata/diff/metadata sequencing.
   - Both files' `::test_redirect_is_not_followed`, `::test_timeout_and_transport_errors_are_retryable_upstream`, `::test_diff_at_limit_is_accepted_and_just_over_is_rejected`, and `::test_chunked_stream_over_byte_ceiling_is_rejected_without_full_read` cover trusted retrieval boundaries.
   - `tests/vcs/test_gitlab.py::test_raw_diffs_is_a_single_unpaginated_request` documents the raw-diff endpoint contract; pagination is not invented for this endpoint.
   - `tests/scripts/test_vcs_stub.py::test_github_adapter_fetches_fixture_revision` and `::test_gitlab_adapter_fetches_fixture_revision` exercise real adapters against the HTTP stub. Smoke cases 1/3 use configured stub origins; the live check exercises actual GitHub reads.
7. **Revision and completeness** — races/truncation fail explicitly or retry without successful partial context.
   - `tests/vcs/test_github.py::test_head_or_base_changing_during_fetch_raises`, `::test_expected_head_mismatch_raises_before_diff`, and `::test_file_count_mismatch_is_incomplete` cover GitHub races/incompleteness.
   - `tests/vcs/test_gitlab.py::test_revision_changing_during_fetch_raises`, `::test_diff_refs_not_matching_sha_is_retryable_not_ready`, `::test_overflow_flags_fail_closed`, `::test_unprovable_changes_count_is_incomplete`, and `::test_file_count_mismatch_is_incomplete` cover GitLab races/truncation.
   - `tests/ingestion/test_ingest.py::test_retryable_error_schedules_retry_with_backoff`, `::test_retries_exhaust_to_failed`, `::test_terminal_error_fails_immediately`, and `::test_snapshot_for_other_head_is_retried_as_revision_change` connect typed failures to durable outcomes and absence of successful review work.
   - No deliberate race/truncation smoke case; passing success smoke is not evidence for these failures.

### context-level-one/spec.md

8. **Structured revision context** — valid changes preserve file/hunk/line semantics.
   - `tests/context/test_parser.py::test_numbers_content_and_multiple_hunks` verifies line content, kinds, old/new numbers, hunk sections, and schema round-trip; `::test_addition_deletion_and_multiple_files` covers additions/deletions.
   - `tests/context/test_parser_edge_cases.py::test_quoted_non_ascii_and_escaped_paths`, `::test_rename_only_has_paths_and_no_hunks`, `::test_rename_with_changes_keeps_hunks`, and `::test_no_newline_markers_attach_to_preceding_line_only` cover rename/path/newline semantics.
   - API flow and smoke cases 1/3 verify persistence, while parser unit tests provide detailed semantic assertions.
9. **Excluded files** — retain paths/reasons and no reviewable hunks.
   - `tests/context/test_parser_edge_cases.py::test_excluded_files_keep_paths_and_reasons_without_hunks` asserts binary, lockfile, `.min.js`, and `.min.mjs` exclusions, including added/deleted paths and a reviewable control file.
   - No dedicated excluded-file smoke case.
10. **Invalid diff rejection** — inconsistent hunk counts fail, with no successful context.
    - `tests/context/test_parser.py::test_invalid_or_incomplete_patch_raises` includes count inconsistencies and incomplete lines; `tests/context/test_parser_edge_cases.py::test_combined_diff_fixture_rejected` and `::test_unsupported_or_malformed_patches_raise` cover unsupported patches.
    - `tests/ingestion/test_ingest.py::test_parse_error_is_terminal_and_creates_nothing` asserts FAILED and zero repository/MR/job/context rows for malformed input. The worker fixture uses a malformed hunk header; the count-mismatch assertion is at the parser seam, not a separate count-mismatch worker fixture.
    - `tests/context/test_parser.py::test_empty_diff` and `tests/ingestion/test_ingest.py::test_empty_diff_is_valid` cover the requirement's valid-empty boundary. No malformed-diff smoke case.

## Live provider evidence (separate from simulation)

Performed 2026-10-12 by the user, GitHub PR18 `pull_request` / `synchronize`. User reported acceptance at **14:42:07 GMT** and redelivery returning **duplicate at 14:43:33 GMT**. Parent verified database state:

- Provider delivery id `18c952d0-c64b-11f1-83fe-c726db541515`: **SUCCEEDED**, attempts **1**.
- Exactly one review job, `6d03bf60-267c-4215-8f72-1691a3db1411`: **PENDING**.
- Exactly one context: **3812 tokens**, **15249 characters**.

This is supplied user/parent evidence for real GitHub delivery, ingestion, and sequential redelivery idempotency. The job remaining PENDING is consistent with this change's ingestion-only scope. No LLM execution, findings, summary comment, live GitLab check, production deployment, or production-secret configuration is claimed. Specific stored SHAs/schema fields were not included in the supplied live record; detailed revision/schema assertions come from the passing automated tests and simulated smoke, rather than invented live query results.

## Artifact reconciliation

Proposal's nine What Changes bullets map to delivered artifacts:

1. Authentication/endpoints: `app/api/webhooks.py`, `app/webhooks/auth.py`; authenticate raw bytes before normalization, signed/token modes explicit, no signed-mode fallback.
2. Supported events: `app/webhooks/github.py`, `app/webhooks/gitlab.py`; opened/synchronize and open/source-changing update; ignored events create no work.
3. Normalized identity: `app/ingestion/events.py`, provider normalizers, and `app/models/vcs_delivery.py`; provider-scoped transport and revision identities.
4. Retrieval: `app/vcs/github.py`, `app/vcs/gitlab.py`; configured origins, metadata/diff/metadata checks, typed failures and limits.
5. Parsing/exclusions: `app/context/`, parser fixtures/tests; malformed/combined rejection and valid empty diffs.
6. Persistence/recovery: `app/models/`, `alembic/versions/`, `app/ingestion/`, `app/worker/ingestion.py`, `app/worker/recovery.py`; database uniqueness, leases, atomic job/context writes, recovery dispatch.
7. Configuration/tests/architecture: `.env.example`, `app/config.py`, `tests/`, `docs/VCS_SETUP.md`, `docs/BACKEND_ARCHITECTURE.md`, `docs/architecture/erd.md`, and `docs/SYSTEM_DESIGN.md`.
8. Bot deployment context: `docs/VCS_SETUP.md` describes the machine-user account, collaborator/token setup, and reviewer assignment independent of event triggering. Account setup instructions are not proof of production configuration.
9. Verification paths: `scripts/replay_webhook.py`, `scripts/vcs_stub.py`, `scripts/smoke_webhooks.sh`, `docker-compose.smoke.yml`, CI compose job, optional tunnel and host commands in `docs/WORKFLOW.md`. Simulation and live evidence remain distinct.

Acceptance has passing simulated backend-to-context evidence plus the separately supplied live GitHub result. LLM review, publication, cloning, higher context levels, and UI remain outside this change.

Design matches authentication, revision/completeness checks, context schema, durable acceptance/recovery, machine-user setup, and optional tunnel. Its ports/adapters boundary still conflicts with direct SQLAlchemy/RQ imports in ingestion (see blockers). Parent reconciliation suggestion, not applied silently: append to design § Verification and deployment, “The CI compose smoke uses locally signed provider fixtures and a configured stub VCS upstream; live provider evidence is reported separately.” Production env separation now uses `app_env_base`, `api_env`, and `worker_env`; the phase plan's older shared-`app_env`/same-worker-env wording is historical planning, not the current configuration.

Task status inspected: 1.1–5.3 checked; 5.4 unchecked. No checkbox was edited. Parent owns completion, final review, archive, commits, and PR.

Documentation cross-check: README and VCS_SETUP defaults agree with `app/config.py`; `.env.example` lists every environment setting read there. WORKFLOW describes local tunnel/simulation; DEVOPS describes production `/api` routing, `VCS_*` mapping, and recovery; BACKEND_ARCHITECTURE, ERD, SYSTEM_DESIGN, and `openspec/project.md` identify implemented ingestion and pending review/publication. Recovery exists in `infra/main.tf`, uses the same image, restart `unless-stopped`, base env only, and depends on migrate/Redis. API receives webhook secrets; worker receives VCS tokens. This confirms source wiring, not production runtime state.

Necessary factual corrections made in this task:

- `docs/VCS_SETUP.md`: accepted means durable storage plus attempted dispatch, not guaranteed enqueue; unsigned deploy-smoke 401 does not prove missing/configured secrets or signed ingestion readiness.
- `docs/BACKEND_ARCHITECTURE.md`: implemented ingestion no longer implies that the target service/repository boundary is fully implemented.
- `docs/DEVOPS.md`: pending-delivery troubleshooting checks due time/error before attributing it to stopped processes.

Existing specs outside this change were inspected and not edited: `background-worker` specifies the ping smoke job and remains valid, but its purpose does not describe ingestion/recovery. `review-data` relationships/status constraints remain valid, but its entity inventory omits the delivery inbox and structured context additions. Parent should reconcile these descriptive omissions during spec reconciliation; neither spec explicitly forbids the new behavior.

## Security and log source sweep

Read-only inspection covered webhook handlers/auth/normalizers, ingestion/dispatch/recovery, VCS/context modules, replay/smoke scripts, configuration, and deployment staging. No `.env`, production secret value, full live payload, raw diff, or model output was read or copied into this report.

Token-prefix search over tracked files found protocol placeholders/prefix checks, redaction regexes, test fixtures, and the labelled throwaway smoke signing key. Concrete synthetic tokens are confined to tests and the smoke override; docs/plans contain placeholder `whsec_` syntax. No production-looking credential was identified in that scoped inspection. This is a source sweep, not a claim of complete repository-history secret scanning; the parent's final command/audit output is pending if required.

Explicit webhook logs contain provider/outcome/action/delivery id and fixed validation or ignored-event reasons. Payload errors name schema fields without reflecting field values; configuration errors do not print signing values. VCS/parser modules have no matching explicit logger statements. Dispatch/recovery use `logger.exception` with ids/fixed messages: third-party exception text and tracebacks remain a separate exposure surface, so this report does not certify arbitrary exception content as redacted. `last_error` is bounded/redacted; `tests/ingestion/test_ingest.py::test_last_error_is_redacted_and_bounded` tests token/header/URL credential removal. Redaction of stored errors does not prove traceback redaction.

Queue jobs carry only the delivery identifier. Replay signing secrets come from env, not a raw secret CLI flag. **Replay stdout disclosure is a required phase-review finding; its fix is underway.** The earlier assertion that replay output was limited safely to response/id or fixed transport errors is withdrawn. Replay output safety is not certified until the parent supplies the regression result for the fix. Smoke reads throwaway env into child env; its failure path captures replay output, so replay output disclosure also matters for smoke diagnostics. Deployment secrets enter the staging step via `env`, are serialized with `jq`, and cross SSH via stdin into a 0600 tfvars file; they are not interpolated from `${{ secrets.* }}` directly into the shell body or SSH command arguments. `jq --arg` does place values in the local process arguments, so the existing “never as arguments” staging comment should be interpreted as SSH transport, not a universal process-list guarantee. No change to that deployment design was made here.

Phase-4 residual risks remain visible in VCS_SETUP §8: recovery availability, ASCII-only GitLab token secrets, display-only unvalidated repository URLs, and GitHub rename identity. Tests/CI never execute reviewed PR code; ingestion stores/parses it as untrusted data.

## Outstanding gates and decisions for the parent

- **Branch CI gate satisfied for `03a8f1f`:** `checks`, `terraform`, and `compose` succeeded in the linked run, verified by the parent through the jobs API. Obtain updated checks for subsequent fixes before claiming the final revision is verified.
- **Replay stdout disclosure blocker:** required phase-review fix underway. Parent must provide passing disclosure regression evidence and verification of the resulting revision; output safety remains unverified until then.
- **Final documentation verification:** rerun pre-commit after these edits and append the factual result. All reported passes above preceded these documentation edits.
- **Architecture conflict:** ingestion directly imports SQLAlchemy and dispatch directly imports RQ, contrary to AGENTS.md and design's service/repository/ports boundaries. BACKEND_ARCHITECTURE explicitly records this as unapproved, pending a team decision. This implementer cannot resolve it with documentation-only edits; parent must resolve/escalate it before claiming full artifact compliance. A repository/queue-port extraction is the documented follow-up, not an automatically accepted exception.
- **Production readiness unverified:** actual production secrets and deployed runtime are unknown. Optional empty credentials and fail-closed endpoints are approved design, not an invented deferral or missing-secret assertion. Keep production readiness separate from local feature acceptance; do not report that the user set or knowingly deferred secrets without evidence.
- **Existing domain divergence:** MergeRequest/ReviewJob statuses and per-head uniqueness differ from SYSTEM_DESIGN ReviewRun/rerun semantics. This is already documented and outside this ingestion change; no migration was attempted.
- **Provisional team decisions:** event matrix, body/context limits, freshness, lease, retry/backoff, and recovery timing remain labelled provisional. This verification does not settle them.
- Parent owns phase/final review and design/spec reconciliation. No archival recommendation is made while the replay disclosure finding and architecture conflict remain unresolved.

## Review fix checkpoint

Replay response diagnostics no longer print server bodies or user-supplied expectation text. Ten reflected-authentication/body regression cases were added. Parent verification after this fix: full isolated PostgreSQL 16 / Redis 8 suite **530 passed, 0 skipped**, pre-commit all-files **PASS**. Further final-review fixes and their checks remain pending.

CI for replay/evidence revision `a23f33f`: all required jobs **PASS**, [run 38266965585](https://github.com/larchanka-training/dmc-268-api-t3/actions/runs/38266965585). Later security/architecture fixes must receive their own final CI verification.

## Final-review fix checkpoint (ports extraction and composition module)

This section supersedes the "Architecture conflict" blocker and the statement that dispatch/recovery use `logger.exception` above; those lines are kept as history.

- **Architecture conflict resolved.** Ingestion services (`app/ingestion/service.py`, `dispatch.py`) now depend only on the ports in `app/ingestion/ports.py`. SQLAlchemy lives in `app/repositories/ingestion.py`, RQ in `app/worker/ingestion_queue.py`, and `app/composition/ingestion.py` is the only module wiring them (used by `app/api/webhooks.py`, `app/worker/ingestion.py`, `app/worker/recovery.py`). `app/ingestion/__init__.py` exports event/result types only, which removed the import cycle through `app.worker`. `app/worker/ingestion_runtime.py` was removed.
- **Logging.** Dispatch no longer uses `logger.exception`; it logs a fixed message, the delivery id and the exception class name, never the exception text or traceback. Deterministic data errors store `DataError: Value cannot be stored (<SQLSTATE>)`, without the driver message.
- **Regression tests.** `tests/webhooks/test_framework_free.py` runs fresh interpreters: `app.repositories.ingestion`, `app.worker.ingestion_queue` and `app.composition.ingestion` each import first without error, and `app.ingestion.*` and `app.webhooks.*` load none of sqlalchemy, rq, redis, fastapi, starlette. It failed first (no composition module, `app.ingestion` pulled in `app.worker`).
- **Checks.** Full suite against the throwaway PostgreSQL/Redis containers: 535 passed, 0 skipped. `uv run pre-commit run --all-files`: all hooks passed (ruff, ruff format, mypy strict, pylint). Fresh-interpreter `python -c "import <module>"` over every module under `app/`: no failures.
