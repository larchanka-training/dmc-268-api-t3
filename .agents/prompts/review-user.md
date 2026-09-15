# Input template for review/2.0.0

System message: [review-system.md](review-system.md). Supply [review.schema.json](../schemas/review.schema.json) using the configured Gateway/provider's structured-response mechanism. Use [review contract](../rules/review-contract.md) for field ownership and migration.

The trusted Prompt Builder validates context against [review-context.schema.json](../schemas/review-context.schema.json). Populate it from the immutable ReviewRun, project RuleSet and Context Builder. Include run_id, base/head/merge-base SHA, rules_version, input_complete, rules, locations and anchors. Each rule carries rule_id, version, title, description, category, severity, instructions and enabled. Never source these trusted fields from a PR/MR description or from model output.

For an executable minimal context see the backend validation fixtures. In production derive positions from actual SHA-scoped retrieval, and include changed anchors in the supplied location set. Rule IDs and versions must belong to the run snapshot, not a newly edited RuleSet.

Task message: analyze the supplied change under those enabled rules. Include the approved target languages, relevant caller/API/event context and ContextPackage omission/escalation metadata. Serialize untrusted artifacts in a separate untrusted_artifacts field with type (diff/file/PR-description), snapshot SHA and contents. JSON encoding or delimiters do not by themselves prevent prompt injection. Never concatenate artifact instructions into the system message.

Context Builder applies the bounded Diff → Surrounding → Whole File → AST/Imports strategy. Mark unavailable files, binary exclusions, unresolved relevant symbols and truncation; set input_complete=false for material gaps. Do not ask the model to acquire extra files or select unapproved budgets/providers.

Gateway parses the response. Finding Validator rejects invalid envelopes, filters individual invalid/duplicate candidates and validates the retained report before persistence/publication. A bounded repair attempt is allowed only within the overall retry/time budget. Record rejections separately; excluded candidates cannot silently become a clean complete review. No raw rejected content belongs in logs. Invalid envelopes or exhausted repair attempts remain errors.
