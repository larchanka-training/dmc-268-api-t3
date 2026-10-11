import enum
import logging
import uuid
from datetime import datetime, timedelta

from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.ingestion.service import DEFAULT_LEASE, Clock, SessionFactory, utc_now
from app.models import VcsDelivery, VcsDeliveryStatus
from app.queue import get_queue

logger = logging.getLogger(__name__)

INGESTION_JOB = "app.worker.ingestion.run_ingestion"
DEFAULT_STALE_AFTER = timedelta(minutes=5)
DEFAULT_BATCH_SIZE = 100


class DispatchOutcome(enum.StrEnum):
    ENQUEUED = "enqueued"
    ALREADY_QUEUED = "already_queued"
    FAILED = "failed"


def job_id_for(delivery_id: uuid.UUID) -> str:
    return f"ingest-{delivery_id}"


def _existing_job(queue: Queue, job_id: str) -> Job | None:
    try:
        return Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError:
        return None


def _enqueue(queue: Queue, delivery_id: uuid.UUID) -> DispatchOutcome:
    job_id = job_id_for(delivery_id)
    existing = _existing_job(queue, job_id)
    if existing is not None:
        # Still waiting in the list: a worker will pick it up. A "queued" hash whose
        # id is no longer in the list (worker died right after popping) is stale.
        if (
            existing.get_status() == "queued"
            and queue.connection.lpos(queue.key, job_id) is not None
        ):
            return DispatchOutcome.ALREADY_QUEUED
        # RQ keeps finished/failed jobs (and a crashed worker's "started" one) under
        # their id; a fresh job with the same deterministic id needs the stale one gone.
        existing.delete()
    queue.enqueue(
        INGESTION_JOB,
        str(delivery_id),
        job_id=job_id,
        # Stop the job before its DB lease would expire so a live worker never
        # overlaps with a recovery re-dispatch of the same row.
        job_timeout=int(DEFAULT_LEASE.total_seconds()),
    )
    return DispatchOutcome.ENQUEUED


def enqueue_delivery(
    factory: SessionFactory,
    delivery_id: uuid.UUID,
    *,
    queue: Queue | None = None,
    clock: Clock = utc_now,
) -> DispatchOutcome:
    """Hand an accepted delivery to RQ; never raises into the caller.

    The delivery is already durable, so a queue outage only means it stays
    undispatched for the recovery dispatcher to pick up.
    """
    try:
        outcome = _enqueue(queue or get_queue(), delivery_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not enqueue delivery %s; recovery will retry", delivery_id)
        return DispatchOutcome.FAILED
    try:
        with factory() as session, session.begin():
            session.execute(
                update(VcsDelivery)
                .where(VcsDelivery.id == delivery_id)
                .values(last_dispatched_at=clock())
            )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not record dispatch of delivery %s", delivery_id)
    return outcome


def _needs_dispatch(now: datetime, stale_after: timedelta):  # type: ignore[no-untyped-def]
    """SQL condition: due now and not dispatched since it became due, or stale.

    ``marker`` is when the row last became claimable again (retry time or lease
    expiry); a dispatch at or after it already covers that attempt.
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


def dispatch_pending(
    factory: SessionFactory,
    queue: Queue | None = None,
    *,
    clock: Clock = utc_now,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Re-enqueue deliveries that need (another) job; returns how many were enqueued.

    Covers accepted-not-dispatched, dispatched-but-unclaimed (stale), retries
    that became due, and processing rows whose lease expired. Retry bounds live
    in the ingestion claim (attempts exhausted becomes ``failed``), so a row
    cannot be dispatched forever. Stops at the first failed enqueue (queue down).
    """
    with factory() as session:
        ids = _candidates(session, clock(), stale_after, batch_size)
    queue = queue or get_queue()
    enqueued = 0
    for delivery_id in ids:
        outcome = enqueue_delivery(factory, delivery_id, queue=queue, clock=clock)
        if outcome == DispatchOutcome.FAILED:
            break
        if outcome == DispatchOutcome.ENQUEUED:
            enqueued += 1
    return enqueued
