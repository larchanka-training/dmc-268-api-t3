# Background Worker

## Purpose

Describe the existing worker smoke job used to verify queue processing.

## Requirements

### Requirement: Execute an importable RQ smoke job
The worker SHALL execute the importable `ping` job and return its message prefixed with `pong: `.

#### Scenario: Ping job runs
- **WHEN** the worker executes `ping` with a message
- **THEN** the job returns `pong: ` followed by that message
