import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from redis import Redis
from rq import Queue
from rq.job import Job, JobStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion import AcceptOutcome, IngestOutcome, accept_delivery, ingest_delivery
from app.ingestion.dispatch import (
    DispatchOutcome,
    accept_and_dispatch,
    dispatch_pending,
    enqueue_delivery,
    job_id_for,
)
from app.models import ContextPayload, ReviewJob, VcsDelivery, VcsDeliveryStatus
from app.queue import get_queue
from app.vcs import VcsUpstreamError
from tests.ingestion.conftest import FakeClock, FakeVcsClient, make_event

Factory = Callable[[], Session]
STALE = timedelta(minutes=5)


@pytest.fixture()
def queue(redis_server: Redis) -> Queue:
    return get_queue(redis_server)


def _accept(factory: Factory, **overrides: object) -> uuid.UUID:
    with factory() as session:
        return accept_delivery(session, make_event(**overrides)).delivery_id


def _row(factory: Factory, delivery_id: uuid.UUID) -> VcsDelivery:
    with factory() as session:
        return session.get_one(VcsDelivery, delivery_id)


def _dispatch(factory: Factory, queue: Queue, clock: FakeClock) -> int:
    return dispatch_pending(factory, queue, clock=clock, stale_after=STALE)


def test_enqueue_uses_deterministic_job_id_and_records_dispatch(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)

    outcome = enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    assert outcome == DispatchOutcome.ENQUEUED

    assert queue.job_ids == [f"ingest-{delivery_id}"]
    assert job_id_for(delivery_id) == f"ingest-{delivery_id}"
    assert _row(session_factory, delivery_id).last_dispatched_at == clock.now


def test_enqueue_failure_leaves_delivery_pending_and_does_not_raise(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    unreachable = get_queue(Redis.from_url("redis://127.0.0.1:1/0"))

    assert (
        enqueue_delivery(session_factory, delivery_id, queue=unreachable, clock=clock)
        == DispatchOutcome.FAILED
    )

    row = _row(session_factory, delivery_id)
    assert row.status == VcsDeliveryStatus.PENDING
    assert row.last_dispatched_at is None


def test_accepted_not_dispatched_is_picked_up_by_dispatcher(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)

    assert _dispatch(session_factory, queue, clock) == 1

    assert queue.job_ids == [f"ingest-{delivery_id}"]
    assert _dispatch(session_factory, queue, clock) == 0  # freshly dispatched


def test_dispatched_not_claimed_is_redispatched_only_when_stale_and_job_gone(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    _dispatch(session_factory, queue, clock)

    clock.advance(minutes=6)
    assert _dispatch(session_factory, queue, clock) == 0  # job still queued: no duplicate
    assert queue.job_ids == [f"ingest-{delivery_id}"]

    clock.advance(minutes=6)
    queue.connection.delete(queue.key)  # Redis lost the job
    queue.connection.delete(f"rq:job:ingest-{delivery_id}")
    assert _dispatch(session_factory, queue, clock) == 1
    assert queue.job_ids == [f"ingest-{delivery_id}"]


def test_claimed_then_worker_died_is_redispatched_after_lease_expiry(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    dying = FakeVcsClient(RuntimeError("worker killed"))
    with pytest.raises(RuntimeError):
        ingest_delivery(session_factory, dying, delivery_id, clock=clock)
    assert _row(session_factory, delivery_id).status == VcsDeliveryStatus.PROCESSING

    clock.advance(minutes=4)
    assert _dispatch(session_factory, queue, clock) == 0  # lease still held

    clock.advance(minutes=2)
    assert _dispatch(session_factory, queue, clock) == 1
    assert queue.job_ids == [f"ingest-{delivery_id}"]


def test_retry_scheduled_delivery_waits_for_next_attempt_at_then_redispatches(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    _dispatch(session_factory, queue, clock)
    queue.connection.delete(queue.key)
    clock.advance(seconds=1)
    flaky = FakeVcsClient(VcsUpstreamError("boom"))
    result = ingest_delivery(session_factory, flaky, delivery_id, clock=clock)
    assert result.outcome == IngestOutcome.RETRY_SCHEDULED
    due = _row(session_factory, delivery_id).next_attempt_at
    assert due is not None

    clock.advance(seconds=10)  # before the backoff elapses
    assert _dispatch(session_factory, queue, clock) == 0

    clock.now = due
    assert _dispatch(session_factory, queue, clock) == 1  # not blocked by the earlier dispatch
    assert queue.job_ids == [f"ingest-{delivery_id}"]


def test_finished_earlier_job_with_same_id_does_not_block_reenqueue(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    job = Job.fetch(job_id_for(delivery_id), connection=queue.connection)
    job.set_status(JobStatus.FINISHED)
    queue.remove(job)

    outcome = enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    assert outcome == DispatchOutcome.ENQUEUED

    assert queue.job_ids == [f"ingest-{delivery_id}"]
    assert Job.fetch(job_id_for(delivery_id), connection=queue.connection).get_status() == "queued"


def test_duplicate_enqueue_keeps_a_single_queued_job(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)

    first = enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    second = enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    assert (first, second) == (DispatchOutcome.ENQUEUED, DispatchOutcome.ALREADY_QUEUED)

    assert queue.job_ids == [f"ingest-{delivery_id}"]


def test_failed_and_succeeded_deliveries_are_never_dispatched(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    ok = _accept(session_factory, delivery_id="ok")
    ingest_delivery(session_factory, FakeVcsClient(), ok, clock=clock)
    with session_factory() as session, session.begin():
        session.get_one(VcsDelivery, ok).last_dispatched_at = None
    clock.advance(hours=1)

    assert _dispatch(session_factory, queue, clock) == 0
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ContextPayload)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewJob)) == 1


def test_accept_and_dispatch_commits_then_enqueues_a_new_delivery(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    result = accept_and_dispatch(session_factory, make_event(), queue=queue, clock=clock)

    assert result.outcome == AcceptOutcome.CREATED
    assert queue.job_ids == [job_id_for(result.delivery_id)]
    assert _row(session_factory, result.delivery_id).last_dispatched_at == clock.now


def test_accept_and_dispatch_does_not_re_enqueue_duplicates(
    session_factory: Factory, queue: Queue, clock: FakeClock
) -> None:
    first = accept_and_dispatch(session_factory, make_event(), queue=queue, clock=clock)
    assert queue.job_ids == [job_id_for(first.delivery_id)]

    same_delivery = accept_and_dispatch(session_factory, make_event(), queue=queue, clock=clock)
    same_revision = accept_and_dispatch(
        session_factory, make_event(delivery_id="d2"), queue=queue, clock=clock
    )

    assert same_delivery.outcome == AcceptOutcome.DUPLICATE_DELIVERY
    assert same_revision.outcome == AcceptOutcome.DUPLICATE_REVISION
    assert {same_delivery.delivery_id, same_revision.delivery_id} == {first.delivery_id}
    assert queue.job_ids == [job_id_for(first.delivery_id)]


def test_accept_and_dispatch_keeps_the_row_when_the_queue_is_down(
    session_factory: Factory, clock: FakeClock
) -> None:
    unreachable = get_queue(Redis.from_url("redis://127.0.0.1:1/0"))

    result = accept_and_dispatch(session_factory, make_event(), queue=unreachable, clock=clock)

    assert result.outcome == AcceptOutcome.CREATED
    row = _row(session_factory, result.delivery_id)
    assert row.status == VcsDeliveryStatus.PENDING
    assert row.last_dispatched_at is None
