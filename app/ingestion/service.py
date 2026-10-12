import re
import uuid
from datetime import UTC, datetime, timedelta

from app.context import DiffParseError, parse_diff
from app.ingestion.events import (
    IngestOutcome,
    IngestResult,
)
from app.ingestion.ports import (
    Claim,
    Clock,
    DeliveryRepository,
    DeliveryStatus,
    IngestionDataError,
    PreparedContext,
)
from app.llm.tokens import approximate_token_count
from app.vcs import (
    PullRequestRef,
    VcsClient,
    VcsDiffIncompleteError,
    VcsError,
    VcsRevisionChangedError,
)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE = timedelta(minutes=5)
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(hours=1)
RETRY_AFTER_CAP = timedelta(hours=24)
LAST_ERROR_LIMIT = 2000


def utc_now() -> datetime:
    return datetime.now(UTC)


_REDACTIONS = (
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(
        r"(?i)\b(authorization|private-token|token|secret|password|api[_-]?key)\s*[:=]\s*\S+"
    ),
    re.compile(r"\b(?:gh[pousr]_|github_pat_|glpat-)[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"),
)


def redact(message: str) -> str:
    for pattern in _REDACTIONS:
        message = pattern.sub("[REDACTED]", message)
    return message


def _describe(error: Exception) -> str:
    if isinstance(error, IngestionDataError):
        return f"DataError: Value cannot be stored ({error.cause})"
    message = str(error)
    return f"{type(error).__name__}: {redact(message)}"[:LAST_ERROR_LIMIT]


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, VcsRevisionChangedError | VcsDiffIncompleteError):
        return True
    return isinstance(error, VcsError) and error.retryable


def _backoff(attempts: int, error: Exception) -> timedelta:
    wait: timedelta = min(BACKOFF_BASE * 2 ** (attempts - 1), BACKOFF_CAP)
    hint = error.retry_after if isinstance(error, VcsError) else None
    if hint is not None:
        wait = max(wait, min(timedelta(seconds=hint), RETRY_AFTER_CAP))
    return wait


def _claim(
    repository: DeliveryRepository,
    delivery_id: uuid.UUID,
    now: datetime,
    lease: timedelta,
    max_attempts: int,
) -> tuple[IngestResult | None, Claim | None]:
    """Short transaction: take the lease, or report why the row is not claimable."""
    with repository.claimable(delivery_id, now) as row:
        if row is None:
            return IngestResult(IngestOutcome.NOT_CLAIMED), None
        if row.attempts >= max_attempts:
            row.status = DeliveryStatus.FAILED
            row.locked_until = None
            row.next_attempt_at = None
            row.last_error = "AttemptsExhausted: attempts exhausted after lease expiry"
            return IngestResult(IngestOutcome.FAILED), None
        row.status = DeliveryStatus.PROCESSING
        row.attempts += 1
        row.locked_until = now + lease
        return None, Claim(row.event, now + lease)


def _fail_or_retry(  # pylint: disable=too-many-locals
    repository: DeliveryRepository,
    delivery_id: uuid.UUID,
    claim: Claim,
    error: Exception,
    now: datetime,
    max_attempts: int,
) -> IngestResult:
    with repository.held(delivery_id, claim) as row:
        if row is None:
            return IngestResult(IngestOutcome.LEASE_LOST)
        row.last_error = _describe(error)
        row.locked_until = None
        if _is_retryable(error) and row.attempts < max_attempts:
            row.status = DeliveryStatus.PENDING
            row.next_attempt_at = now + _backoff(row.attempts, error)
            return IngestResult(IngestOutcome.RETRY_SCHEDULED)
        row.status = DeliveryStatus.FAILED
        row.next_attempt_at = None
        return IngestResult(IngestOutcome.FAILED)


def ingest_delivery(  # pylint: disable=too-many-locals
    repository: DeliveryRepository,
    client: VcsClient,
    delivery_id: uuid.UUID,
    *,
    clock: Clock = utc_now,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    lease: timedelta = DEFAULT_LEASE,
) -> IngestResult:
    """Claim, fetch, parse and atomically commit one inbox delivery.

    No DB transaction spans the HTTP fetch: the claim is a short transaction that
    leases the row, and the final write is guarded by that lease. Typed VCS
    errors, parse errors and deterministic data errors (``IngestionDataError``: a value
    the schema can never store) follow the retry/terminal policy; anything else,
    including transient database errors, propagates and the row is recovered
    when its lease expires.
    """
    early, claim = _claim(repository, delivery_id, clock(), lease, max_attempts)
    if claim is None:
        assert early is not None
        return early
    event = claim.event
    try:
        snapshot = client.fetch_revision(
            PullRequestRef(
                provider=event.provider,
                repository=event.repository_external_id,
                number=event.pr_number,
                expected_head_sha=event.head_sha,
            )
        )
        if snapshot.head_sha != event.head_sha:
            raise VcsRevisionChangedError("retrieved head differs from the delivery head")
        context = parse_diff(snapshot.raw_diff)
        payload = PreparedContext(
            snapshot=snapshot,
            context=context,
            token_count=approximate_token_count(snapshot.raw_diff),
            provider_metadata={
                "provider": snapshot.provider,
                "repository": snapshot.repository,
                "number": snapshot.number,
                "title": snapshot.title,
                "state": snapshot.state,
                "head_ref": snapshot.head_ref,
                "base_ref": snapshot.base_ref,
                "delivery_id": event.delivery_id,
                "action": event.action,
            },
        )
        job_id = repository.save_success(delivery_id, claim, payload)
        return (
            IngestResult(IngestOutcome.SUCCEEDED, job_id)
            if job_id is not None
            else IngestResult(IngestOutcome.LEASE_LOST)
        )
    except (VcsError, DiffParseError, IngestionDataError) as error:
        return _fail_or_retry(repository, delivery_id, claim, error, clock(), max_attempts)
