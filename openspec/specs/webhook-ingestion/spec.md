# webhook-ingestion Specification

## Purpose
Describe how authenticated GitHub and GitLab webhook deliveries are verified, normalized, durably accepted with deduplication, and dispatched to the worker with recovery.

## Requirements

### Requirement: Authenticated delivery
The system MUST authenticate original webhook bytes before processing supported provider payloads.

#### Scenario: Authenticated delivery
- **WHEN** a GitHub HMAC signature or configured GitLab authentication is invalid
- **THEN** the endpoint rejects the request and creates no delivery or review job

### Requirement: Supported source events
The system SHALL accept GitHub opened/synchronize PR events and GitLab open/source-commit-update MR events.

#### Scenario: Supported source events
- **WHEN** an authenticated unrelated event arrives
- **THEN** the endpoint acknowledges it without creating ingestion work

### Requirement: Durable idempotent acceptance
The system SHALL persist accepted delivery work durably and deduplicate both provider deliveries and source revisions atomically.

#### Scenario: Durable idempotent acceptance
- **WHEN** the same delivery or source revision is received concurrently
- **THEN** at most one ingestion job/context is created for that source revision

### Requirement: Dispatch recovery
The system MUST recover accepted work after a database-to-queue dispatch failure.

#### Scenario: Dispatch recovery
- **WHEN** Redis is unavailable after durable acceptance
- **THEN** the delivery remains recoverable and is dispatched when the queue recovers

### Requirement: Persisted context
The system SHALL persist raw diff, metadata, structured Context Level 1, and revision identity for successful ingestion.

#### Scenario: Persisted context
- **WHEN** a valid supported webhook completes ingestion
- **THEN** its review job has a complete structured context available for subsequent processing
