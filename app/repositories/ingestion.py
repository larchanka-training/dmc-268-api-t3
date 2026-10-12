import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.ingestion.events import AcceptOutcome, AcceptResult, VcsEvent
from app.ingestion.ports import (
    Claim,
    DeliveryState,
    DeliveryStatus,
    IngestionDataError,
    PreparedContext,
)
from app.models import (
    ContextPayload,
    MergeRequest,
    Repository,
    ReviewJob,
    VcsDelivery,
    VcsDeliveryStatus,
)
from app.vcs import RevisionSnapshot

SessionFactory = Callable[[], Session]


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


def _held(session: Session, delivery_id: uuid.UUID, claim: Claim) -> VcsDelivery | None:
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
    claim: Claim,
    payload: PreparedContext,
) -> uuid.UUID | None:
    """One transaction: repository, MR, job, context and delivery ``succeeded``."""
    event = claim.event
    snapshot = payload.snapshot
    context = payload.context
    with factory() as session, session.begin():
        row = _held(session, delivery_id, claim)
        if row is None:
            return None
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
                    token_count=payload.token_count,
                    structured_context=context.model_dump(mode="json"),
                    provider_metadata=payload.provider_metadata,
                    schema_version=context.schema_version,
                    head_sha=snapshot.head_sha,
                    base_sha=snapshot.base_sha,
                )
            )
        row.status = VcsDeliveryStatus.SUCCEEDED
        row.locked_until = None
        row.next_attempt_at = None
        row.last_error = None
        return job_id


def _needs_dispatch(now: datetime, stale_after: timedelta):  # type: ignore[no-untyped-def]
    """SQL condition: due now and not dispatched since it became due, or stale.

    A dispatch at or after the time the row last became claimable again (the
    retry time or the lease expiry) already covers that attempt.
    """
    stale = or_(
        VcsDelivery.last_dispatched_at.is_(None),
        VcsDelivery.last_dispatched_at <= now - stale_after,
    )
    pending = and_(
        VcsDelivery.status == VcsDeliveryStatus.PENDING,
        or_(VcsDelivery.next_attempt_at.is_(None), VcsDelivery.next_attempt_at <= now),
        or_(stale, VcsDelivery.last_dispatched_at < VcsDelivery.next_attempt_at),
    )
    expired = and_(
        VcsDelivery.status == VcsDeliveryStatus.PROCESSING,
        VcsDelivery.locked_until <= now,
        or_(stale, VcsDelivery.last_dispatched_at < VcsDelivery.locked_until),
    )
    return or_(pending, expired)


def _candidates(
    session: Session, now: datetime, stale_after: timedelta, limit: int
) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(VcsDelivery.id)
            .where(_needs_dispatch(now, stale_after))
            .order_by(VcsDelivery.created_at)
            .limit(limit)
        )
    )


class SqlAlchemyDeliveryRepository:
    def __init__(self, factory: SessionFactory) -> None:
        self.factory = factory

    def accept(self, event: VcsEvent) -> AcceptResult:
        with self.factory() as session:
            return accept_delivery(session, event)

    @contextmanager
    def claimable(self, delivery_id: uuid.UUID, now: datetime) -> Iterator[DeliveryState | None]:
        with self.factory() as session, session.begin():
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
            yield from self._state(row)

    @contextmanager
    def held(self, delivery_id: uuid.UUID, claim: Claim) -> Iterator[DeliveryState | None]:
        with self.factory() as session, session.begin():
            yield from self._state(_held(session, delivery_id, claim))

    @staticmethod
    def _state(row: VcsDelivery | None) -> Iterator[DeliveryState | None]:
        if row is None:
            yield None
            return
        state = DeliveryState(
            _event_of(row),
            DeliveryStatus(row.status),
            row.attempts,
            row.locked_until,
            row.next_attempt_at,
            row.last_error,
        )
        yield state
        # Reached only on normal exit of the caller's ``with`` block (an exception
        # raised into the generator skips this), so the transaction then rolls back
        # without the yielded-state changes; the enclosing commit persists them.
        row.status = VcsDeliveryStatus(state.status)
        row.attempts = state.attempts
        row.locked_until = state.locked_until
        row.next_attempt_at = state.next_attempt_at
        row.last_error = state.last_error

    def save_success(
        self, delivery_id: uuid.UUID, claim: Claim, payload: PreparedContext
    ) -> uuid.UUID | None:
        try:
            return _commit_success(self.factory, delivery_id, claim, payload)
        except DataError as error:
            # Value-free cause only: the SQLSTATE if the driver gives one, else the class.
            code = getattr(error.orig, "sqlstate", None) or type(error.orig).__name__
            raise IngestionDataError(str(code)) from None

    def mark_dispatched(self, delivery_id: uuid.UUID, now: datetime) -> None:
        with self.factory() as session, session.begin():
            session.execute(
                update(VcsDelivery)
                .where(VcsDelivery.id == delivery_id)
                .values(last_dispatched_at=now)
            )

    def candidates(self, now: datetime, stale_after: timedelta, limit: int) -> list[uuid.UUID]:
        with self.factory() as session:
            return _candidates(session, now, stale_after, limit)
