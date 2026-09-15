# Review candidate contract 2.0.0

This local contract aligns prompts/templates with System Design v1. It describes LLM candidates, not an approved API response or database row. The design does not fix wire field names, confidence thresholds, or partial-history policy; choices below are explicit package conventions.

## Boundaries and ownership

| Layer | Data and responsibility |
| --- | --- |
| Trusted input | run_id, base/head/merge-base SHA, rules_version, immutable rules, supplied locations, changed anchors, input completeness |
| LLM candidate report | Versioned envelope, coverage, findings with rule_id, primary anchor, related_changed_lines, evidence/recommendation and confidence; hypotheses for missing context |
| Validated domain Finding | Server assigns finding_id/run linkage; maps scenario/expected/actual/impact into description and suggestion into recommendation; preserves rule_id, location, evidence and candidate confidence |
| FindingMatcher | Assigns lifecycle and links to prior findings; owns matching confidence separately |
| API DTO | Backend contract exposes run lifecycle, coverage, publication and history as separate fields; frontend validates that DTO |

Rule/model/prompt versions come from trusted run state. The report echoes rules_version and immutable SHAs for consistency checks. It cannot assign IDs, lifecycle, enable rules, change their severity, or choose its own baseline. A related changed line is a relation to the diff, not a request for an inline comment.

## Trusted context

Use [review-context.schema.json](../schemas/review-context.schema.json). `rules` contains the run's supplied versioned rule records, including enabled flags, title, description, category, severity and instructions. Only enabled rule IDs may be referenced; candidate severity must equal that rule's configured severity. A rule absent from the snapshot is invalid even if it exists elsewhere in the project. An empty rule list permits no findings.

`locations` lists exact supplied source positions verified by the Context Builder/VCS reader. `anchors` lists the changed positions and must be a subset of locations. Each position is path, LEFT/RIGHT side, and positive line number: LEFT is the base snapshot and RIGHT the head snapshot, using side-correct paths for renames. All input artifacts must correspond to these snapshots. Untrusted code cannot add locations or rules.

A candidate's primary anchor may be an unchanged supplied line if its evidence belongs there; related_changed_lines must contain at least one trusted changed anchor. This supports related-code defects without inventing lines. Both lists are bounded upstream by the context budget; JSON validation does not verify source retrieval or the truth of evidence.

## Envelope and candidate validation

Use [review.schema.json](../schemas/review.schema.json). The `findings` array contains candidates until application validation completes. Required candidate additions in 2.0.0 are rule_id, related_changed_lines and confidence (0–1, a model estimate, never proof). Findings must follow the enabled rules; the signature catalog is only supporting evidence guidance.

Reject the whole response on malformed JSON/envelope, duplicate JSON keys, identity/version mismatch, invalid trusted context, or inconsistent coverage. Incomplete input or missing-context hypotheses cannot claim complete. Failed coverage contains neither findings nor hypotheses; transport failures remain run errors owned by Gateway/Worker.

For a valid envelope, check each candidate's structure, rule membership/severity, locations, and changed relations. In filtering mode exclude invalid candidates while preserving valid ones in order. Return safe zero-based candidate indices and reason codes separately, never raw rejected text in diagnostics. Any invalid-candidate rejection makes surviving coverage partial with an explicit limitation, even when no findings remain; it must not appear as a clean review.

Suppress exact duplicate candidate objects deterministically, keeping the first. Duplicate suppression alone does not reduce coverage. Similar wording or shared locations do not prove duplicate root cause: the prompt combines repetitions, and broader matching/deduplication requires its owned policy. The local validator makes no claim to detect semantic paraphrases.

Strict validation rejects any invalid or exact-duplicate candidate. Filtering returns a validated report plus rejections; persist/publish only the report findings, and keep rejection diagnostics outside the model schema. Never pass the unfiltered candidates directly to Publisher.

## Coverage, lifecycle, and history

Coverage complete/partial/failed is distinct from ReviewRun NEW/QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED and separate publication status. A completed review may have partial coverage or failed publication under the owning API policy. Do not infer this mapping from spelling alone.

Findings are candidates concerning the supplied change and rules. This call alone does not determine NEW/PERSISTING/FIXED. Matching/history needs prior validated findings and adequate current context. Until the owning policy specifies partial-baseline eligibility and absence evidence, incomplete analysis must not produce FIXED decisions.

## Migration from 1.0.0

2.0.0 is a breaking package contract: new envelope fields merge_base_sha/rules_version; new candidate fields rule_id/related_changed_lines/confidence; trusted rules and locations; and primary anchors allowed in supplied related context. Both repositories carry identical schemas/prompts and this document.

Update Prompt Builder, trusted context assembly, validator callers, fixtures, and consuming adapters together. Reprocess or explicitly migrate old reports with their original rule snapshot; do not invent rule IDs or silently label 1.0.0 as 2.0.0. The strict validator intentionally rejects 1.0.0 input. Preserve historical reports with their original version. Database/API serialization is a separate owning contract.
