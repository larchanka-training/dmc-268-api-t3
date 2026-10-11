import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.context import DiffParseError, parse_diff
from app.ingestion.events import (
    AcceptOutcome,
    AcceptResult,
    IngestOutcome,
    IngestResult,
    VcsEvent,
)
from app.llm.tokens import approximate_token_count
from app.models import (
    ContextPayload,
    MergeRequest,
    Repository,
    ReviewJob,
    VcsDelivery,
    VcsDeliveryStatus,
)
from app.vcs import (
    PullRequestRef,
    RevisionSnapshot,
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

SessionFactory = Callable[[], Session]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def accept_delivery(session: Session, event: VcsEvent) -> AcceptResult:
    """Persist the inbox row idempotently; commits.

    ``ON CONFLICT DO NOTHING`` covers both the ``(provider, delivery_id)`` and the
    revision identity constraints, so concurrent duplicates never raise. The
    winning row is committed (the conflicting insert waits for it), so the
    follow-up lookup tells the two duplicate kinds apart.
    """
    created_id = session.execute(
        insert(VcsDelivery)
        .values(
            provider=event.provider,
            delivery_id=event.delivery_id,
            repository_external_id=event.repository_external_id,
            pr_number=event.pr_number,
            action=event.action,
            head_sha=event.head_sha,
            base_sha=event.base_sha,
            repository_name=event.repository_name,
            repository_url=event.repository_url,
            title=event.title,
            author=event.author,
            source_branch=event.source_branch,
            target_branch=event.target_branch,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing()
        .returning(VcsDelivery.id)
    ).scalar_one_or_none()
    session.commit()
    if created_id is not None:
        return AcceptResult(AcceptOutcome.CREATED, created_id)

    by_delivery = session.scalar(
        select(VcsDelivery.id).where(
            VcsDelivery.provider == event.provider,
            VcsDelivery.delivery_id == event.delivery_id,
        )
    )
    if by_delivery is not None:
        return AcceptResult(AcceptOutcome.DUPLICATE_DELIVERY, by_delivery)
    by_revision = session.scalars(
        select(VcsDelivery.id).where(
            VcsDelivery.provider == event.provider,
            VcsDelivery.repository_external_id == event.repository_external_id,
            VcsDelivery.pr_number == event.pr_number,
            VcsDelivery.head_sha == event.head_sha,
        )
    ).one()
    return AcceptResult(AcceptOutcome.DUPLICATE_REVISION, by_revision)


@dataclass(frozen=True)
class _Claim:
    event: VcsEvent
    lease: datetime  # the lease token: only the holder may finish the row


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
    return f"{type(error).__name__}: {redact(str(error))}"[:LAST_ERROR_LIMIT]


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


def _event_of(row: VcsDelivery) -> VcsEvent:
    return VcsEvent(
        provider=row.provider,
        delivery_id=row.delivery_id,
        repository_external_id=row.repository_external_id,
        pr_number=row.pr_number,
        action=row.action,
        head_sha=row.head_sha,
        base_sha=row.base_sha,
        repository_name=row.repository_name,
        repository_url=row.repository_url,
        title=row.title,
        author=row.author,
        source_branch=row.source_branch,
        target_branch=row.target_branch,
    )


def _claim(
    factory: SessionFactory,
    delivery_id: uuid.UUID,
    now: datetime,
    lease: timedelta,
    max_attempts: int,
) -> tuple[IngestResult | None, _Claim | None]:
    """Short transaction: take the lease, or report why the row is not claimable."""
    with factory() as session, session.begin():
        row = session.scalars(
            select(VcsDelivery)
            .where(
                VcsDelivery.id == delivery_id,
                or_(
                    and_(
                        VcsDelivery.status == VcsDeliveryStatus.PENDING,
                        or_(
                            VcsDelivery.next_attempt_at.is_(None),
                            VcsDelivery.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        VcsDelivery.status == VcsDeliveryStatus.PROCESSING,
                        VcsDelivery.locked_until <= now,
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if row is None:
            return IngestResult(IngestOutcome.NOT_CLAIMED), None
        if row.attempts >= max_attempts:
            row.status = VcsDeliveryStatus.FAILED
            row.locked_until = None
            row.next_attempt_at = None
            row.last_error = "AttemptsExhausted: attempts exhausted after lease expiry"
            return IngestResult(IngestOutcome.FAILED), None
        row.status = VcsDeliveryStatus.PROCESSING
        row.attempts += 1
        row.locked_until = now + lease
        return None, _Claim(_event_of(row), now + lease)


def _fail_or_retry(  # pylint: disable=too-many-locals
    factory: SessionFactory,
    delivery_id: uuid.UUID,
    claim: _Claim,
    error: Exception,
    now: datetime,
    max_attempts: int,
) -> IngestResult:
    with factory() as session, session.begin():
        row = _held(session, delivery_id, claim)
        if row is None:
            return IngestResult(IngestOutcome.LEASE_LOST)
        row.last_error = _describe(error)
        row.locked_until = None
        if _is_retryable(error) and row.attempts < max_attempts:
            row.status = VcsDeliveryStatus.PENDING
            row.next_attempt_at = now + _backoff(row.attempts, error)
            return IngestResult(IngestOutcome.RETRY_SCHEDULED)
        row.status = VcsDeliveryStatus.FAILED
        row.next_attempt_at = None
        return IngestResult(IngestOutcome.FAILED)


def _held(session: Session, delivery_id: uuid.UUID, claim: _Claim) -> VcsDelivery | None:
    """Lock the row if this worker still holds the lease it claimed with."""
    return session.scalars(
        select(VcsDelivery)
        .where(
            VcsDelivery.id == delivery_id,
            VcsDelivery.status == VcsDeliveryStatus.PROCESSING,
            VcsDelivery.locked_until == claim.lease,
        )
        .with_for_update()
    ).one_or_none()


def _upsert_repository(session: Session, event: VcsEvent) -> uuid.UUID:
    values = {
        "id": uuid.uuid4(),
        "external_id": f"{event.provider}:{event.repository_external_id}",
        "name": event.repository_name or event.repository_external_id,
        "url": event.repository_url or "",
    }
    statement = insert(Repository).values(**values)
    return session.execute(
        statement.on_conflict_do_update(
            index_elements=[Repository.external_id],
            set_={"name": statement.excluded.name, "url": statement.excluded.url},
        ).returning(Repository.id)
    ).scalar_one()


def _upsert_merge_request(
    session: Session, event: VcsEvent, repository_id: uuid.UUID, snapshot: RevisionSnapshot
) -> uuid.UUID:
    values = {
        "id": uuid.uuid4(),
        "repository_id": repository_id,
        "external_id": str(event.pr_number),
        "title": snapshot.title,
        "source_branch": snapshot.head_ref,
        "target_branch": snapshot.base_ref,
        "author": event.author or "",
    }
    statement = insert(MergeRequest).values(**values)
    updates = {
        "title": statement.excluded.title,
        "source_branch": statement.excluded.source_branch,
        "target_branch": statement.excluded.target_branch,
    }
    if event.author:
        updates["author"] = statement.excluded.author
    return session.execute(
        statement.on_conflict_do_update(
            constraint="uq_merge_requests_repository_external_id", set_=updates
        ).returning(MergeRequest.id)
    ).scalar_one()


def _commit_success(  # pylint: disable=too-many-locals
    factory: SessionFactory,
    delivery_id: uuid.UUID,
    claim: _Claim,
    snapshot: RevisionSnapshot,
) -> IngestResult:
    """One transaction: repository, MR, job, context and delivery ``succeeded``."""
    event = claim.event
    context = parse_diff(snapshot.raw_diff)
    with factory() as session, session.begin():
        row = _held(session, delivery_id, claim)
        if row is None:
            return IngestResult(IngestOutcome.LEASE_LOST)
        repository_id = _upsert_repository(session, event)
        merge_request_id = _upsert_merge_request(session, event, repository_id, snapshot)
        job_id = session.execute(
            insert(ReviewJob)
            .values(
                id=uuid.uuid4(),
                merge_request_id=merge_request_id,
                head_sha=snapshot.head_sha,
                base_sha=snapshot.base_sha,
            )
            .on_conflict_do_nothing(constraint="uq_review_jobs_merge_request_head")
            .returning(ReviewJob.id)
        ).scalar_one_or_none()
        if job_id is None:
            # Final guard: this revision already has its job and context.
            job_id = session.scalars(
                select(ReviewJob.id).where(
                    ReviewJob.merge_request_id == merge_request_id,
                    ReviewJob.head_sha == snapshot.head_sha,
                )
            ).one()
        else:
            session.add(
                ContextPayload(
                    review_job_id=job_id,
                    diff_text=snapshot.raw_diff,
                    token_count=approximate_token_count(snapshot.raw_diff),
                    structured_context=context.model_dump(mode="json"),
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
                    schema_version=context.schema_version,
                    head_sha=snapshot.head_sha,
                    base_sha=snapshot.base_sha,
                )
            )
        row.status = VcsDeliveryStatus.SUCCEEDED
        row.locked_until = None
        row.next_attempt_at = None
        row.last_error = None
        return IngestResult(IngestOutcome.SUCCEEDED, job_id)


def ingest_delivery(  # pylint: disable=too-many-locals
    factory: SessionFactory,
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
    errors and parse errors follow the retry/terminal policy; anything else
    propagates and the row is recovered when its lease expires.
    """
    early, claim = _claim(factory, delivery_id, clock(), lease, max_attempts)
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
        return _commit_success(factory, delivery_id, claim, snapshot)
    except (VcsError, DiffParseError) as error:
        return _fail_or_retry(factory, delivery_id, claim, error, clock(), max_attempts)
