## ADDED Requirements

### Requirement: Bounded provider retrieval
The system SHALL retrieve PR/MR metadata and raw diff from configured GitHub or GitLab API origins with credentials, timeouts, and size limits.

#### Scenario: Bounded provider retrieval
- **WHEN** a supported source event is ingested
- **THEN** the adapter obtains metadata and diff without trusting webhook-provided URLs

### Requirement: Revision and completeness
The system MUST persist context only for a consistent source/base revision and a complete provider diff.

#### Scenario: Revision and completeness
- **WHEN** the source revision changes during retrieval or the provider truncates the diff
- **THEN** ingestion records an explicit failure or bounded retry without presenting the context as complete
