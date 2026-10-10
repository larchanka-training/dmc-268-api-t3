# Implementation Plan: Context Level 1 — Phase 1

## Overview

Implement only tasks 1.1 and 1.2 of add-vcs-webhooks-context. This phase supplies a pure parser and serializable typed contract without HTTP, database, or worker changes. OpenSpec tasks.md owns implementation status.

## Architecture Decisions

- Place Pydantic contracts in app/context/schemas.py and a pure parse_diff entry point in app/context/parser.py.
- Preserve line content without patch prefixes; context lines have both numbers, additions only new, deletions only old.
- Validate each hunk's declared old/new counts; never silently accept a truncated patch.
- Preserve excluded file records with explicit reasons. Use known lockfile names and minified JS suffixes, not broad generated-file heuristics.

## Task 1.1: Typed parser foundation

Description: Define schema_version, file/hunk/line schemas, a typed parse error, and unified-diff state machine.

Acceptance criteria:
- Additions, deletions, and context lines carry exact line numbers across multiple files and hunks, including omitted counts and zero-length ranges.
- Empty input produces a valid empty context; invalid ranges, orphan patch lines, and incomplete hunks produce a typed error.
- Models serialize to JSON without loss of paths or line content.

Verification: Run focused tests with `rtk uv run pytest tests/context -q`, Ruff checks/format and strict mypy for the touched modules.

Dependencies: None.

Files likely touched: app/context/__init__.py, schemas.py, parser.py, tests/context/test_parser.py.

Estimated scope: Medium.

## Task 1.2: Git patch edge cases and exclusions

Description: Extend the parser for rename-only patches, git-quoted paths, final-newline markers, binary records, lock files, and *.min.js / *.min.mjs.

Acceptance criteria:
- Quoted paths, rename-only changes, added/deleted files, and newline markers preserve correct semantics.
- Binary and generated exclusions retain paths/reasons but no reviewable hunks.
- Combined diffs and malformed quoting fail explicitly; literal content resembling patch metadata inside hunks remains content.

Verification: Extend fixture-based tests, run all context tests and touched-module lint/types, then run the existing suite as the phase checkpoint.

Dependencies: 1.1.

Files likely touched: app/context/parser.py, schemas.py, tests/context/test_parser.py, tests/context/fixtures/.

Estimated scope: Medium.

## Checkpoint

Verify each task before updating tasks.md. Delegate each checkbox to a fresh subagent, sequentially; parent verifies and handles status/commits under the agreed commit mode. Before implementation the user reviews this phase plan. Do not plan later phases here.

## Risks and Mitigations

- Git quoting can encode UTF-8 octal bytes: decode according to git path conventions and test non-ASCII fixtures.
- Binary patches can include opaque payloads: recognize file boundaries without parsing binary content as hunks.
- Overbroad filters can hide source: use explicit names/suffixes with observable exclusion reasons.

## Open Questions

Commit mode is awaiting the user's selection. No implementation requires provider credentials in this phase.
