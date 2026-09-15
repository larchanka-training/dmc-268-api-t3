# Error signatures: how to test a hypothesis

A signature is a suspicious pattern, not a diagnosis. Apply it only to a changed, reachable execution path.

| Signature | How to demonstrate the defect | When it is not a finding |
| --- | --- | --- |
| SELECT → INSERT for a run | Two workers pass the check; no unique constraint or atomic upsert exists | A database constraint exists and conflicts are handled |
| Deduplication by SHA alone | A second explicit rerun returns the old run_id, violating the contract | This is a file cache, not run deduplication |
| Ack before a durable save | A crash in this window loses the only copy of the work | A durable inbox has already accepted the work |
| A synchronous SDK call inside async code | Actual blocking I/O runs on the event loop | The call is asynchronous or runs in a thread pool |
| JWT decoded but not verified | An unsigned or modified token grants access | Middleware has already verified the token |
| Loading a run by id alone | A user from another tenant receives the report | The repository applies tenant scoping before returning data |
| Retrying publishReviewSummary | A repeated request creates a second visible comment | The marked summary and external comment ID are reconciled and reused across retries/reruns |
| `as DTO` for JSON | Invalid JSON reaches application logic and breaks the contract | Runtime validation has already occurred at the boundary |
| An old fetch writes to the store | Slow response A replaces the already selected B | A generation guard or cancellation with a freshness check exists |
| Model error → findings=[] | The UI shows a successful review after a timeout | A separate error status prevents that interpretation |

Example evidence: “With two concurrent deliveries of event E, both transactions see no record and create different run_id values because the event key is not unique.” First establish that the supplied migration lacks the constraint. If migrations are unavailable, record a hypothesis requesting that context.

Do not label every SQL string as SQL injection: a parameterized query is a counterexample. Do not infer service unavailability from the name of a timeout without tracing the failure path.

## Security and language-specific evidence

Use these checks only when the changed code and its callers establish a reachable path. Review the actual escaping, validation, authorization, locking, and lifetime boundaries before confirming a defect.

| Area | Evidence to establish | Counterexample to check |
| --- | --- | --- |
| SQL or command injection | Untrusted input reaches executable query/command syntax | Bound query parameters or argument arrays with appropriate command policy |
| Path traversal | A user-controlled path escapes the permitted root and reaches file access | Canonical-path containment and resource authorization |
| SSRF | User-controlled destinations reach unrestricted network requests, including redirects | Enforced destination policy across resolution and redirects |
| Unsafe HTML rendering | Untrusted report or PR content reaches an HTML execution sink | Escaped text rendering or correctly applied sanitization |
| Secrets in diagnostics | An actual secret-bearing field is emitted on a reachable logging path | Redaction occurs before serialization and every relevant sink |
| Go concurrency/resources | Conflicting shared-memory access, a blocked channel, or a missing close is reachable | Synchronization, ownership, cancellation, and cleanup cover the path |
| Java authorization/resources | A caller can bypass a resource check or leak a resource on an exceptional path | Shared authorization middleware and structured cleanup cover the path |
| JavaScript/TypeScript async flow | An unhandled rejection or stale completion causes a visible failure | Await/catch/cancellation and generation guards cover both outcomes |
| Python async/transactions | Blocking I/O stalls the loop or a failed transaction is reused without recovery | Async/thread-pool boundaries and rollback/session ownership are correct |
| PHP comparison/input handling | Type coercion or unsafe input handling changes an access or data decision | Strict comparison, validated types, and bound parameters preserve the contract |

Report a single root cause once even if several signatures match. For unavailable caller or framework context, ask a precise question in hypotheses and mark coverage partial rather than inventing a missing safeguard.

## Design v1 regression signatures

Apply only when an enabled supplied rule covers the defect; the catalog does not define rule IDs.

| Candidate | Evidence required | Counterexample |
| --- | --- | --- |
| Deduplication uses only delivery_id | Distinct deliveries for the same automatic business identity create two runs | PostgreSQL enforces the automatic business key separately |
| Context cache mixes snapshots | Same path returns content from a different repository/SHA/context parameter set | The cache key includes all identity and context dimensions |
| Rule snapshot changes mid-run | A finding references a new/disabled rule or severity instead of the run snapshot | Immutable RuleSet and membership/severity checks cover the path |
| Every rerun creates a comment | A new run creates another summary despite an existing marker | Publisher updates/reconciles the marked comment |
| Failed or incomplete run clears history | Missing findings from failed/partial analysis mark prior findings FIXED | Eligible successful baseline and sufficient absence evidence are required |
| Publication failure overwrites review success | A saved completed review becomes inaccessible as a failed analysis | ReviewPublication and ReviewRun states remain separate |
| Context budget is bypassed by expansion | Whole-file/import expansion exceeds the shared budget | Every escalation consumes the same configured budget |
