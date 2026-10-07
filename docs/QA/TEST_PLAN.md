
---

# TEST_PLAN.md

**Project:** AI Code Reviewer  
**Version:** 1.0 (Draft for team review)  
**Scope:** v1 (GitHub + GitLab)  
**Related documents:** `SYSTEM_DESIGN.md` v1.0  
**Owner:** QA / Test Engineer  
**Status:** Draft

---

## 1. Purpose and Scope

This document defines the strategy, levels, metrics, and artifacts for testing the AI Code Reviewer system. The goal is to ensure reproducible, measurable, and secure review quality at all levels: from VCS adapters to evaluation of the LLM component's quality.

### 1.1. Testing Principles

Aligned with the principles of `SYSTEM_DESIGN.md`:

- **Determinism at boundaries.** Everything that is not the LLM is tested deterministically (unit/integration). The LLM component is tested statistically.
- **Provider isolation.** Core tests must not depend on GitHub/GitLab; adapters are tested separately against recorded fixtures and (optionally) sandbox environments.
- **Immutable snapshot.** Tests must verify that a `ReviewRun` does not "drift" to a new SHA during execution.
- **Untrusted LLM output.** All LLM outputs in tests pass through the `Finding Validator` — we test both "good" and "malicious" responses.
- **Secure by default.** Tests for logging, credentials, and prompt injection are part of the Definition of Done, not optional.

### 1.2. Out of Scope for v1 Testing

- Load testing at the "1000 PR/min" scale (outside v1 NFRs).
- Penetration testing of VCS providers.
- Evaluating the quality of LLM models as a product (vendor comparison) — only applied review metrics.

---

## 2. Testing Levels

The pyramid is adapted for an AI system: classical levels + a separate **LLM Evaluation** contour.

```
        ┌──────────────────────────┐
        │   E2E (staging)          │  ← real VCS sandbox + LLM provider
        ├──────────────────────────┤
        │   LLM Evaluation         │  ← MR dataset + offline metrics
        ├──────────────────────────┤
        │   Integration            │  ← adapters, MQ, DB, LLM Gateway (mock/real)
        ├──────────────────────────┤
        │   Unit                   │  ← core, parsers, rules, validator, matcher
        └──────────────────────────┘
```

### 2.1. Unit tests

**Goal:** fast verification of logic without I/O.

**What we cover:**
- `Event Normalizer`: normalization of GitHub/GitLab payload → `NormalizedEvent`.
- `Trigger Policy`: decision table (opened/synchronized/reopened → start/skip).
- Deduplication: construction of `review_deduplication_key`, behavior on unique constraint conflict.
- `Context Builder`: escalation levels (Diff → Surrounding → Whole File → AST), trimming by token budget, formation of `ContextPackage`.
- AST parsing (Tree-sitter): extraction of symbols, imports, ranges.
- `Finding Validator`: required fields, valid `rule_id`, file/line consistency with the snapshot, deduplication of candidates.
- `Finding Matcher`: match scoring, rejection at low confidence.
- Parser of the structured LLM response (including malformed JSON).
- `ReviewPublication` formatter (stable marker `<!-- ai-code-review:summary -->`).
- Rendering of domain models from normalized payloads.

**Requirements:**
- Core coverage: **≥ 85%** lines and **≥ 75%** branches (for modules without LLM).
- All tests deterministic, no network or DB.
- LLM in unit tests — only through the mock interface `LlmGateway`.

**Tools:** Jest/Vitest (TS), pytest (if Python), golden files for parsers.

### 2.2. Integration tests

**Goal:** verify component interaction across real boundaries (DB, MQ, HTTP).

**What we cover:**
- `VcsReader`/`VcsPublisher`: GitHubAdapter/GitLabAdapter against **recorded fixtures** (VCR approach) and, optionally, against sandbox repositories.
- Webhook endpoint: signature validation, rejection on invalid signature, idempotency by `delivery_id`.
- Orchestrator + PostgreSQL: creation of `ReviewRun`, uniqueness of `review_deduplication_key`, race on parallel inserts.
- Redis/RQ: enqueue of `{run_id}`, worker behavior on a duplicate job, retries, failed-job registry (DLQ).
- Worker + DB: status transitions `NEW → QUEUED → RUNNING → COMPLETED|FAILED|CANCELLED`, recovery of stale `RUNNING`.
- `LlmGateway` with **recorded responses** (fixture-based) and with a mock provider returning errors/timeouts/malformed responses.
- `VcsPublisher` + DB: `ReviewPublication` with `COMPLETED` run and `FAILED` publication — independence of states.
- Retention/secrets: verification that queue messages contain no credentials or code.

**Requirements:**
- All integration tests use **testcontainers** (PostgreSQL, Redis).
- VCS fixtures are stored in the repository and versioned.
- Mandatory negative scenarios: network unavailable, VCS returns 5xx, MQ unavailable at enqueue time, DB unavailable at run load time.

### 2.3. E2E tests

**Goal:** verify the full user path in the staging environment.

**Scenarios:**
1. **GitHub happy path:** opening a test PR → webhook → run → summary comment appears in the PR.
2. **GitLab happy path:** same for MR.
3. **Rerun without SHA changes:** new `run_id`, update of the existing comment (by marker), no duplicate creation.
4. **New push (SHA B) during review execution for SHA A:** the run for A correctly completes on snapshot A; a separate run for B is started.
5. **Manual rerun:** a new `run_id` is created even if the SHA has not changed.
6. **Comment manually deleted:** publisher creates a new one.
7. **History:** finding "PERSISTING" on re-detection, "FIXED" on disappearance in the next successful run.
8. **Failed run does not become baseline:** comparison is made with the last **successful** run.
9. **LLM provider failure:** run → `FAILED`, no publication, incident visible in metrics.
10. **Publication failure:** run → `COMPLETED`, publication → `FAILED`, retry of publication does not restart the review.

**Environment:** staging with sandbox repositories on GitHub and GitLab, real LLM provider (or cassette mode).

**Frequency:** nightly + on release candidates.

### 2.4. LLM Evaluation

A separate contour, since the LLM component is non-deterministic and requires statistical metrics.

**Goals:**
- Measure finding quality (Precision / Recall / F1).
- Assess hallucinations and false positives.
- Assess completeness of coverage of known defects.
- Detect prompt injection and leaks.

**Dataset (see §4):** a set of test MRs with **labeled expected findings** (ground truth) and **control "clean" MRs** without defects.

**Metrics:** §3.

**Run:** offline, with fixed `rules_version`, `model_version`, `prompt_version`. Each run is saved as a `ReviewRun` in an isolated DB.

---

## 3. Methodology for Testing the LLM Component

### 3.1. Ground Truth and Labeling

Each test MR receives a label:

```text
expected_findings:
  - rule_id
    file
    line_range
    severity
    bug_type          # security | logic | style | perf | ...
    must_find: true|false   # critical for Recall
    optional: true|false    # do not affect Recall, counted as noise
```

Labeling is performed by two reviewers independently, discrepancies resolved by a third (adjudication). Stored in `testdata/mr_dataset/<id>/expected.json`.

### 3.2. Quality Metrics

Matching LLM findings with ground truth — by the rules of `FindingMatcher` (rule_id + file + line range + evidence similarity). A match is considered valid when the threshold is exceeded; the threshold is fixed in the test config and versioned.

| Metric | Formula | v1 Target (proposal) |
|---|---|---|
| **Precision (finding-level)** | TP / (TP + FP) | ≥ 0.70 |
| **Recall (finding-level)** | TP / (TP + FN) | ≥ 0.60 |
| **F1** | 2·P·R / (P+R) | ≥ 0.65 |
| **Recall for must_find** | TP_must / (TP_must + FN_must) | ≥ 0.85 |
| **False Positive Rate (FPR)** | FP / (number of files in review) | ≤ 0.15 |
| **Hallucination Rate** | findings with non-existent file/line/symbols / all findings | ≤ 0.05 |
| **Rule Attribution Accuracy** | findings with correct `rule_id` / all findings | ≥ 0.90 |
| **Duplicate Rate** | duplicates after `FindingMatcher` / all findings | ≤ 0.05 |
| **Actionability Rate** | findings with evidence + recommendation / all findings | = 1.00 |
| **Injection Resistance** | prompt-injection attempts that did not affect behavior / all attempts | = 1.00 |
| **Severity Calibration** | severity match with ground truth (±1 level) / matched findings | ≥ 0.75 |

> Numeric thresholds are a **QA proposal** and require team approval (see §7).

### 3.3. Classification of LLM Errors

All deviations are classified to separate model, prompt, and validator issues:

1. **False Positive (FP)** — a finding not present in ground truth.
   - `FP_hallucination` — points to a non-existent file/line/symbol.
   - `FP_misread` — real code, but interpretation is incorrect.
   - `FP_style` — subjective/style remark outside the rules.
   - `FP_duplicate` — duplicate of an already found issue.
2. **False Negative (FN)** — expected finding not detected.
   - `FN_must_find` — critical (security, crash).
   - `FN_optional` — non-critical.
3. **Hallucination** — invented context: files, functions, APIs, imports that do not exist.
4. **Prompt injection** — code/comments in the MR attempting to override instructions.
5. **Schema violation** — response does not parse or violates the contract.

For each type — a separate counter in the run report, linked to `run_id`, `model_version`, `prompt_version`, `rules_version`.

### 3.4. Run Methodology

- **Stable baseline:** fixed dataset + fixed versions of rules/model/prompt.
- **Repeatability:** 3 runs per configuration; the report includes median and IQR (LLM is non-deterministic).
- **Temperature/seed** (if the provider supports it) are fixed in the test config.
- **A/B prompts:** allowed, but results are compared only on the same dataset and with the same `rules_version`.
- **Regression:** any PR that changes the prompt/rules/response schema triggers a full LLM-eval; a metric degradation > 5 pp is a blocking signal.

### 3.5. Manual Audit

At least **10%** of findings from each run (or a minimum of 30 findings) are reviewed blindly by a human to calibrate automatic metrics. Discrepancies between manual and automatic labeling > 10 pp → revision of matching criteria.

---

## 4. Test Stands and Synthetic Data

### 4.1. Environments

| Environment | Purpose | Features |
|---|---|---|
| **local** | Unit + part of Integration | Testcontainers (Postgres, Redis), mock LLM |
| **ci** | Unit + Integration + metric collection | Testcontainers, fixture LLM |
| **staging** | E2E + LLM Eval | Sandbox GitHub/GitLab orgs, real LLM, isolated DB |
| **prod** | Smoke + canary | Metrics read-only, no synthetic MRs |

### 4.2. Sandbox VCS

- **GitHub:** separate organization `ai-code-reviewer-sandbox`, private repositories with fictitious code, separate service account.
- **GitLab:** separate group, self-managed or gitlab.com (to be clarified in §7), separate bot user.
- Webhook secrets differ from prod.
- Tokens — only in the secret store, rotation quarterly.

### 4.3. Synthetic MR Dataset

Directory structure:

```
testdata/
  mr_dataset/
    <dataset_id>/
      meta.json              # language, category, complexity, labeler
      mr.diff
      files/                 # snapshot of files at head_sha
      expected.json          # ground truth
      injection/             # optional: PRs with prompt-injection attempts
  clean_mrs/                 # MRs without defects (FPR control)
  large_mrs/                 # NFR boundaries: size, file count, lines
```

**Minimum v1 composition (proposal):**

| Category | Number of MRs | Purpose |
|---|---|---|
| Security (SQLi, secrets, XSS) | 15 | must_find, high priority |
| Logic bugs | 15 | must_find |
| Null/edge cases | 10 | must_find |
| Style / minor | 10 | optional, noise check |
| Clean MRs without defects | 20 | FPR |
| Prompt injection | 5 | resistance |
| Multilingual (TS, Python, Go, Java) | 20 | language coverage |
| Large MRs (NFR boundary) | 5 | degradation and budgets |
| Diff-only / surrounded / whole-file | 9 | Context Builder escalation |

**Total:** ~110 MRs. Expanded as the rule catalog grows.

### 4.4. Data Requirements

- **No real secrets or PII.** All tokens/passwords are synthetic.
- **License cleanliness:** code is written from scratch or taken from public domain.
- **Versioning:** the dataset changes only via a PR with QA + rules owner review.
- **Train/eval separation:** the dataset is not used for prompt tuning (otherwise — leakage). For prompt iterations — a separate `dev_dataset`.
- **Language matrix:** minimum 3 languages at start; expansion — via open decision.

### 4.5. Tools

- **Testcontainers** — Postgres, Redis.
- **WireMock / MSW** — HTTP mocks for VCS and LLM.
- **VCR / Polly.js** — recording/playback of real GitHub/GitLab responses.
- **Promptfoo / DeepEval / custom runner** — LLM-eval run and metric aggregation (choice — open decision).
- **pytest / Jest** — frameworks.
- **Grafana + Prometheus** (staging) — run observability.

---

## 5. Test Scenario Templates

### 5.1. Unit/Integration Test Template

```gherkin
Feature: <module>
  Scenario: <name>
    Given <initial state / input data>
    When  <action>
    Then  <expected result>
    And   <invariants / DB / queue state checks>
```

Example:

```gherkin
Feature: Trigger Policy
  Scenario: synchronized event starts a review
    Given NormalizedEvent(provider=github, action=synchronized, head_sha=B)
    When  TriggerPolicy.evaluate(event)
    Then  result == START_REVIEW
    And   review_deduplication_key == "github:repo:pr:B:auto"
```

### 5.2. E2E Test Template

```gherkin
Feature: End-to-end review (GitHub)
  Scenario: PR synchronize during review execution
    Given an open PR #42 at SHA=A and an active ReviewRun(run_id=R1, head_sha=A)
    When  the developer pushes SHA=B
    Then  run R1 completes on snapshot A (does not switch to B)
    And   run R2 is created with head_sha=B
    And   a single summary comment is updated in the PR by marker
```

### 5.3. LLM Evaluation Case Template

```yaml
id: mr-security-sqli-001
language: python
rule_under_test: SEC-SQLI-001
mr: testdata/mr_dataset/security_sqli_001/mr.diff
expected_findings:
  - rule_id: SEC-SQLI-001
    file: app/db.py
    line_range: [42, 45]
    severity: high
    must_find: true
metrics:
  - precision
  - recall
  - hallucination_rate
  - rule_attribution_accuracy
notes: >
  Direct concatenation of parameters into SQL. One high-severity finding expected.
  Additional findings about parameterization are considered FP_style.
```

### 5.4. Security Test Template

```gherkin
Feature: Security boundaries
  Scenario: LLM response does not affect PR comment content outside the schema
    Given LlmGateway returns a response with the field "html": "<script>"
    When  Finding Validator processes the candidates
    Then  ValidatedFinding contains no fields outside the schema
    And   publisher escapes HTML before publication
```

### 5.5. Infrastructure Negative Test Template

```gherkin
Feature: Failure isolation
  Scenario: publication fails, review is not rolled back
    Given ReviewRun(run_id=R) completed successfully with status COMPLETED
    And   VcsPublisher returns 5xx
    Then  ReviewPublication.status == FAILED
    And   ReviewRun.status remains COMPLETED
    And   a retry of publication does not create a new ReviewRun
```

---

## 6. Component Coverage Matrix

| Component (SYSTEM_DESIGN) | Unit | Integration | E2E | LLM Eval |
|---|---|---|---|---|
| Webhook Handler | ✔ | ✔ | ✔ | — |
| Event Normalizer | ✔ | ✔ | — | — |
| Trigger Policy | ✔ | ✔ | ✔ | — |
| Orchestrator / ReviewRun | ✔ | ✔ | ✔ | — |
| VcsReader / Adapters | — | ✔ | ✔ | — |
| VcsPublisher | ✔ | ✔ | ✔ | — |
| Redis (RQ) / Worker | — | ✔ | ✔ | — |
| Context Builder | ✔ | ✔ | — | ✔ |
| AST / Imports | ✔ | ✔ | — | ✔ |
| LLM Gateway | ✔ | ✔ | ✔ | ✔ |
| Finding Validator | ✔ | ✔ | ✔ | ✔ |
| Finding Matcher | ✔ | ✔ | ✔ | ✔ |
| Publication | ✔ | ✔ | ✔ | — |
| Security / Logging | ✔ | ✔ | ✔ | ✔ |

---

## 7. Entry/Exit Criteria and Open Questions

### 7.1. Definition of Done for TEST_PLAN.md

- Testing levels and boundaries between them are agreed.
- LLM quality metrics and thresholds are agreed (see §3.2).
- Minimum MR dataset (§4.3) and its versioning process are agreed.
- Staging environments and sandbox VCS are approved (§4.1–4.2).
- The set of templates is approved (§5).
- Open questions below are assigned to owners with deadlines.

### 7.2. Open Questions (require team decision)

1. **Metric thresholds** (§3.2): approve or adjust Precision/Recall/F1/FPR/Hallucination.
2. **Language matrix** of the dataset: which languages are mandatory in v1.
3. **Choice of LLM-eval framework** (Promptfoo / DeepEval / custom runner).
4. **Test data retention:** storage periods for diff and LLM responses in staging.
5. **GitHub Cloud vs Enterprise** — affects sandbox and E2E.
6. **Retry and DLQ policy** — must be fixed before writing worker integration tests.
7. **Stale `RUNNING` recovery** — TTL and testing strategy.
8. **Finding matching threshold** for LLM-eval — synchronize with the production `FindingMatcher`.
9. **Manual audit share** (§3.5) — 10% or a fixed 30 findings.
10. **Dataset owner** (QA vs rules owner) and SLA for labeling new MRs.
11. **Cost of LLM-eval runs** and nightly token budget.
12. **Canary in prod:** is smoke on real PRs acceptable and with what restrictions.

### 7.3. v1 Release Exit Criteria (Quality Gate)

- Core unit coverage ≥ 85%.
- All integration scenarios green on CI.
- E2E happy path for GitHub and GitLab — green on staging.
- LLM Eval: Precision ≥ threshold, Recall for must_find ≥ threshold, Hallucination Rate ≤ threshold (thresholds from §3.2).
- Injection Resistance = 1.00 on the attack dataset.
- Negative scenarios (VCS/MQ/LLM/publication) — green.
- No leaks of secrets/code in logs (verified by an automatic scanner).

---

## Appendix A. Test PR Review Checklist

- [ ] The test is deterministic (no `sleep`, no network without a mock).
- [ ] Fixtures/dataset from `testdata/` are used, not inline strings.
- [ ] State invariants (DB/queue) are checked, not just the response.
- [ ] A negative case is present for every new happy path.
- [ ] If prompt/rules/LLM schema were changed — an LLM-eval report is attached.
- [ ] Run metrics are attached (for LLM-eval changes).

## Appendix B. Glossary

- **Ground truth** — labeling of expected findings on a test MR.
- **Hallucination** — a finding referencing non-existent context.
- **Must-find** — a defect the system is obligated to detect.
- **Injection Resistance** — the share of prompt-injection attacks that did not change behavior.
- **Stale run** — a `ReviewRun` in `RUNNING` status that has exceeded its TTL.

---

**End of document.**
