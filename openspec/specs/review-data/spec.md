# Review Data

## Purpose

Describe the persisted domain entities and constraints for review data.

## Requirements

### Requirement: Persist review entities and relationships
The persistence layer SHALL represent repositories, merge requests, review jobs, context payloads, and findings with UUID primary keys and the relationships defined by the database models.

#### Scenario: Repository owns merge requests
- **WHEN** a repository is persisted with merge requests
- **THEN** each merge request references that repository and deleting the repository cascades to its merge requests and their review jobs

#### Scenario: Review job stores one context and multiple findings
- **WHEN** a review job is persisted with its results
- **THEN** it has at most one context payload and may have multiple findings
- **AND** deleting the review job cascades to its context payload and findings

### Requirement: Constrain review job status and finding severity
The persistence layer SHALL accept only the declared review job statuses `pending`, `running`, `completed`, and `failed`, and finding severities `info`, `warning`, `error`, and `critical`.

#### Scenario: Valid enum values are stored
- **WHEN** a review job or finding is persisted with a declared enum value
- **THEN** the value is stored as a validated string enum
