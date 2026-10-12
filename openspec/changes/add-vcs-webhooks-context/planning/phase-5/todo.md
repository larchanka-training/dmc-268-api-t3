# Phase 5 working steps

5.2 (ngrok tunnel) is already done (6af2819).

- [x] User reviews the phase 5 plan and answers the open questions: deploy wiring scope, recovery container, secret names, live check, GitLab in production.
- [x] Delegate 5.1a (replay script, unit tests, mypy `files`) to a fresh subagent. Verify it and commit without ticking 5.1.
- [x] Delegate 5.1b (stub VCS, smoke override, smoke script, CI compose job) to a fresh subagent. Verify the local compose smoke, tick 5.1, and commit.
- [x] Run the checkpoint after 5.1: full suite with PostgreSQL/Redis, pre-commit, CI `compose` job.
- [x] Delegate 5.3a (Terraform variables and env, recovery container, ci-cd stage step and deploy smoke) to a fresh subagent. Verify with Terraform fmt/validate and commit.
- [x] Delegate 5.3b (`docs/VCS_SETUP.md`, README, WORKFLOW, DEVOPS, AGENTS link) to a fresh subagent. Verify the cross-check and the live-check report, then commit.
- [x] Delegate 5.3c (BACKEND_ARCHITECTURE, ERD, SYSTEM_DESIGN §5/§15, `project.md`) to a fresh subagent. Verify, tick 5.3, and commit.
- [x] User sets the production secrets in repository settings, or defers them knowingly.
- [x] Delegate 5.4 (all checks, reconciliation, `verification.md`) to a fresh subagent. Verify the results, tick 5.4, and commit.
- [x] Phase review, then the final review. Then the parent validates, archives, and opens the PR.
