# Backend behavior verification plan

Fill in the issue, owning contract/version, Given/When/Then, regression detected, fixtures/cleanup, test level, service prerequisites and actual command/result. Use [testing-system](../prompts/testing-system.md) for direct model calls.

| Scenario | Required observation | Level |
| --- | --- | --- |
| Same technical delivery repeated concurrently | One processing result | API/DB |
| Different deliveries, same automatic provider/repository/PR-or-MR/head/trigger | Same run_id through unique business key | PostgreSQL concurrency |
| Two fresh manual reruns at the same SHA; retry one | Two runs overall, stable identity for the retry | API/DB |
| Incompatible payload for an existing operation key | Explicit conflict without data disclosure | API |
| Webhook state differs from VcsReader snapshot | Run uses authoritative fetched snapshot | Orchestrator unit |
| Crash between DB commit, enqueue and ack | Durable recovery without duplicate run/result | DB/broker |
| Transient versus fatal failure; retry exhaustion | Bounded transient retries, no fatal loop, exhausted message in DLQ | Worker/broker |
| A duplicate worker claims a completed run | Completed state/result remain intact | DB concurrency |
| Context escalates through four levels and reaches budget | Deterministic bounded context and explicit omissions | Context unit |
| Unsupported/external symbol cannot resolve | Material gap recorded without crashing review | Context unit |
| Same path in two repositories/SHAs/context parameter sets | No cross-context cache hit | Context unit |
| Rule edited after run creation | Original RuleSet/version/severity remain in force | Contract/DB |
| Unknown/disabled rule, wrong severity, invented location or relation | Candidate rejected with safe diagnostic | Contract |
| Valid and invalid candidates in one valid envelope | Valid subset preserved; partial coverage | Contract |
| Exact duplicate candidates | First retained; no duplicate publication input | Contract |
| Previous success, intervening failed run, new successful run | Same-PR/MR successful baseline used | Matcher |
| Ambiguous match or partial/missing absence evidence | No guessed link or unsupported FIXED | Matcher |
| Rerun with existing summary; deleted summary | Update existing comment; recreate only after deletion/reconciliation | Publisher |
| Competing old/new publication and uncertain create retry | One summary; old run does not overwrite new one | Publisher integration |
| Saved completed review, publication fails | Review readable as completed; separate failed publication | API/publisher |
| Missing/expired JWT or another tenant's run | Accepted 401/403/404 policy without disclosure | API |
| Gateway timeout/invalid envelope or VCS 429 | Error and bounded budgets; no clean empty success | Gateway/reader |

Mocks verify caller behavior, not PostgreSQL uniqueness or RabbitMQ recovery. Resolve numeric thresholds, retry counts and partial-baseline policy from their owners. Run the affected subset in actual service suites; this template does not claim these integrations are implemented by `.agents`.
