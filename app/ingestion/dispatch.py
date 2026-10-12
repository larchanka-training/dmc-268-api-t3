import enum
import logging
import uuid
from datetime import timedelta

from app.ingestion.events import AcceptOutcome, AcceptResult, VcsEvent
from app.ingestion.ports import Clock, DeliveryQueue, DeliveryRepository
from app.ingestion.service import utc_now

logger = logging.getLogger(__name__)
DEFAULT_STALE_AFTER = timedelta(minutes=5)
DEFAULT_BATCH_SIZE = 100


class DispatchOutcome(enum.StrEnum):
    ENQUEUED = "enqueued"
    ALREADY_QUEUED = "already_queued"
    FAILED = "failed"


def job_id_for(delivery_id: uuid.UUID) -> str:
    return f"ingest-{delivery_id}"


def enqueue_delivery(
    repository: DeliveryRepository,
    delivery_id: uuid.UUID,
    *,
    queue: DeliveryQueue,
    clock: Clock = utc_now,
) -> DispatchOutcome:
    """Hand an accepted delivery to the delivery queue; never raises into the caller.

    The delivery is already durable, so a queue outage only means it stays
    undispatched for the recovery dispatcher to pick up.
    """
    try:
        outcome = (
            DispatchOutcome.ENQUEUED
            if queue.enqueue(delivery_id)
            else DispatchOutcome.ALREADY_QUEUED
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Fixed message plus the class only: exception text may carry connection details.
        logger.error(
            "Could not enqueue delivery %s (%s); recovery will retry",
            delivery_id,
            type(exc).__name__,
        )
        return DispatchOutcome.FAILED
    try:
        repository.mark_dispatched(delivery_id, clock())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "Could not record dispatch of delivery %s (%s)", delivery_id, type(exc).__name__
        )
    return outcome


def accept_and_dispatch(
    repository: DeliveryRepository,
    event: VcsEvent,
    *,
    queue: DeliveryQueue,
    clock: Clock = utc_now,
) -> AcceptResult:
    """Durably accept ``event`` and enqueue its job only for a new delivery.

    The inbox row is committed (and its session closed) before RQ is touched.
    Duplicates are never re-enqueued; a delivery that is stuck is the recovery
    dispatcher's job. Database errors propagate; queue errors do not.
    """
    result = repository.accept(event)
    if result.outcome == AcceptOutcome.CREATED:
        enqueue_delivery(repository, result.delivery_id, queue=queue, clock=clock)
    return result


def dispatch_pending(
    repository: DeliveryRepository,
    queue: DeliveryQueue,
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
    ids = repository.candidates(clock(), stale_after, batch_size)
    enqueued = 0
    for delivery_id in ids:
        outcome = enqueue_delivery(repository, delivery_id, queue=queue, clock=clock)
        if outcome == DispatchOutcome.FAILED:
            break
        if outcome == DispatchOutcome.ENQUEUED:
            enqueued += 1
    return enqueued
