"""The only place that wires the ingestion services to their adapters.

Importable by both the API and the worker. Each function here takes a session
factory (and optionally an RQ queue), builds ``SqlAlchemyDeliveryRepository`` and
``RqDeliveryQueue`` and calls the same-named port-based function in
``app.ingestion.service`` / ``app.ingestion.dispatch``.
"""

import uuid
from datetime import timedelta

from rq import Queue

from app.ingestion import dispatch, service
from app.ingestion.events import AcceptResult, IngestResult, VcsEvent
from app.ingestion.ports import Clock
from app.repositories.ingestion import SessionFactory, SqlAlchemyDeliveryRepository
from app.vcs import VcsClient
from app.worker.ingestion_queue import RqDeliveryQueue


def ingest_delivery(
    factory: SessionFactory,
    client: VcsClient,
    delivery_id: uuid.UUID,
    *,
    clock: Clock = service.utc_now,
    max_attempts: int = service.DEFAULT_MAX_ATTEMPTS,
    lease: timedelta = service.DEFAULT_LEASE,
) -> IngestResult:
    return service.ingest_delivery(
        SqlAlchemyDeliveryRepository(factory),
        client,
        delivery_id,
        clock=clock,
        max_attempts=max_attempts,
        lease=lease,
    )


def enqueue_delivery(
    factory: SessionFactory,
    delivery_id: uuid.UUID,
    *,
    queue: Queue | None = None,
    clock: Clock = service.utc_now,
) -> dispatch.DispatchOutcome:
    return dispatch.enqueue_delivery(
        SqlAlchemyDeliveryRepository(factory),
        delivery_id,
        queue=RqDeliveryQueue(queue),
        clock=clock,
    )


def accept_and_dispatch(
    factory: SessionFactory,
    event: VcsEvent,
    *,
    queue: Queue | None = None,
    clock: Clock = service.utc_now,
) -> AcceptResult:
    return dispatch.accept_and_dispatch(
        SqlAlchemyDeliveryRepository(factory), event, queue=RqDeliveryQueue(queue), clock=clock
    )


def dispatch_pending(
    factory: SessionFactory,
    queue: Queue | None = None,
    *,
    clock: Clock = service.utc_now,
    stale_after: timedelta = dispatch.DEFAULT_STALE_AFTER,
    batch_size: int = dispatch.DEFAULT_BATCH_SIZE,
) -> int:
    return dispatch.dispatch_pending(
        SqlAlchemyDeliveryRepository(factory),
        RqDeliveryQueue(queue),
        clock=clock,
        stale_after=stale_after,
        batch_size=batch_size,
    )
