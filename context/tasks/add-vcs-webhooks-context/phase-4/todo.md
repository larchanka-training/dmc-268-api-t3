# Phase 4 working steps

- [x] User reviews phase 4 plan and confirms open-question defaults (GitLab delivery id fallback, HTTP status semantics, JSON-only GitHub deliveries, deploy wiring ownership).
- [ ] Delegate 4.1 (webhook settings, GitHub/GitLab authentication, payload normalization) to a fresh subagent.
- [ ] Verify 4.1 independently (auth vectors, freshness, token mode, ignored/invalid payloads, no secrets in errors); update OpenSpec status; commit.
- [ ] Delegate 4.2 (endpoints, dependencies, accept_and_dispatch, API and end-to-end tests) to a fresh subagent.
- [ ] Verify 4.2 independently on PostgreSQL and fakeredis/Redis; update OpenSpec status; commit.
- [ ] Run phase checkpoint checks; phase review.
