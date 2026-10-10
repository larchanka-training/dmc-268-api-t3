# Health Check

## Purpose

Describe the HTTP liveness endpoint currently exposed by the API.

## Requirements

### Requirement: API health check
The API SHALL expose `GET /healthcheck` and return HTTP 200 with a JSON object whose `status` is `ok`.

#### Scenario: API is responding
- **WHEN** a client sends `GET /healthcheck`
- **THEN** the API returns HTTP 200 and `{"status":"ok"}`
