# Entity-Relationship Diagram

```mermaid
erDiagram
    REPOSITORY ||--o{ MERGE_REQUEST : has
    MERGE_REQUEST ||--o{ REVIEW_JOB : has
    REVIEW_JOB ||--o| CONTEXT_PAYLOAD : has
    REVIEW_JOB ||--o{ FINDING : has

    REPOSITORY {
        uuid id PK
        string external_id UK "provider:id"
        string name
        string url
        datetime created_at
    }
    MERGE_REQUEST {
        uuid id PK
        uuid repository_id FK "UK with external_id"
        string external_id "UK with repository_id"
        string title
        string source_branch
        string target_branch
        string author
        datetime created_at
    }
    REVIEW_JOB {
        uuid id PK
        uuid merge_request_id FK "UK with head_sha"
        string status
        string head_sha "UK with merge_request_id"
        string base_sha
        string error_message
        datetime created_at
        datetime started_at
        datetime completed_at
    }
    CONTEXT_PAYLOAD {
        uuid id PK
        uuid review_job_id FK "UK"
        text diff_text
        string language
        int token_count
        jsonb structured_context
        jsonb provider_metadata
        int schema_version
        string head_sha
        string base_sha
        datetime created_at
    }
    FINDING {
        uuid id PK
        uuid review_job_id FK
        string file_path
        int line_number
        string severity
        string category
        text message
        datetime created_at
    }

    VCS_DELIVERY {
        uuid id PK
        string provider "UK1 and UK2"
        string delivery_id "UK1"
        string repository_external_id "UK2"
        int pr_number "UK2"
        string action
        string head_sha "UK2"
        string base_sha
        string repository_name
        string repository_url
        string title
        string author
        string source_branch
        string target_branch
        string status
        int attempts
        datetime next_attempt_at
        datetime locked_until
        datetime last_dispatched_at
        text last_error
        datetime created_at
        datetime updated_at
    }
```

`VCS_DELIVERY` is an inbox table and has no foreign keys: it identifies the pull request by the provider-native `repository_external_id` and `pr_number`. Ingestion links a delivery to the relational model only logically, by upserting the `REPOSITORY` (`external_id` = `<provider>:<repository_external_id>`), `MERGE_REQUEST` and `REVIEW_JOB` rows.

Unique constraints (migration `a3c1e5d97b42`):

| Table | Constraint | Columns |
|---|---|---|
| `vcs_deliveries` | `uq_vcs_deliveries_provider_delivery` (UK1) | `provider`, `delivery_id` |
| `vcs_deliveries` | `uq_vcs_deliveries_revision` (UK2) | `provider`, `repository_external_id`, `pr_number`, `head_sha` |
| `merge_requests` | `uq_merge_requests_repository_external_id` | `repository_id`, `external_id` |
| `review_jobs` | `uq_review_jobs_merge_request_head` | `merge_request_id`, `head_sha` |

`vcs_deliveries` also has the index `ix_vcs_deliveries_status_next_attempt_at` on `(status, next_attempt_at)`. `repositories.external_id` and `context_payloads.review_job_id` are unique from the initial schema (the second makes `REVIEW_JOB` to `CONTEXT_PAYLOAD` one-to-one).
