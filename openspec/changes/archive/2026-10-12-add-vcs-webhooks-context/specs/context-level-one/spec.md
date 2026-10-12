## ADDED Requirements

### Requirement: Structured revision context
The system SHALL parse unified diffs into versioned files, hunks, and lines with accurate old/new line numbers and paths.

#### Scenario: Structured revision context
- **WHEN** a valid addition, deletion, modification, or rename diff is parsed
- **THEN** the context preserves file change type, hunk ranges, line content, line kinds, and newline markers

### Requirement: Excluded files
The system SHALL exclude binary files, known lock files, and minified JavaScript from reviewable hunks while retaining exclusion reasons.

#### Scenario: Excluded files
- **WHEN** an excluded file appears in the diff
- **THEN** its paths and exclusion reason are retained without reviewable hunks

### Requirement: Invalid diff rejection
The system MUST reject malformed, incomplete, or unsupported combined patches and SHALL accept an empty diff.

#### Scenario: Invalid diff rejection
- **WHEN** a patch has inconsistent hunk counts
- **THEN** ingestion fails explicitly without storing a successful context
