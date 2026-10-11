import enum
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class VcsEvent:  # pylint: disable=too-many-instance-attributes
    """Normalized, already-verified source event handed to ingestion.

    ``repository_external_id`` is the provider-native repository identity
    (GitHub ``owner/name``, GitLab numeric project id as text); it is stored
    provider-scoped as ``<provider>:<id>`` on ``Repository``.
    """

    provider: str
    delivery_id: str
    repository_external_id: str
    pr_number: int
    action: str
    head_sha: str
    base_sha: str | None = None
    repository_name: str | None = None
    repository_url: str | None = None
    title: str | None = None
    author: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None


class AcceptOutcome(enum.StrEnum):
    CREATED = "created"
    DUPLICATE_DELIVERY = "duplicate_delivery"
    DUPLICATE_REVISION = "duplicate_revision"


@dataclass(frozen=True)
class AcceptResult:
    outcome: AcceptOutcome
    delivery_id: uuid.UUID


class IngestOutcome(enum.StrEnum):
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    NOT_CLAIMED = "not_claimed"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    review_job_id: uuid.UUID | None = None
