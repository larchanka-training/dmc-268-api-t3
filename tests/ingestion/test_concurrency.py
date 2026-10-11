import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion import (
    AcceptOutcome,
    IngestOutcome,
    VcsEvent,
    accept_delivery,
    ingest_delivery,
)
from app.models import ContextPayload, MergeRequest, Repository, ReviewJob, VcsDelivery
from app.vcs import RevisionSnapshot
from tests.ingestion.conftest import FakeClock, FakeVcsClient, make_event, make_snapshot

Factory = Callable[[], Session]
WORKERS = 8


def _run_concurrently[T](count: int, work: Callable[[int], T]) -> list[T]:
    """Release `count` threads from a barrier at once; each gets its own index."""
    barrier = threading.Barrier(count)

    def task(index: int) -> T:
        barrier.wait(timeout=10)
        return work(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return [
            future.result(timeout=30) for future in [pool.submit(task, i) for i in range(count)]
        ]


def _count(factory: Factory, model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _accept_all(factory: Factory, events: list[VcsEvent]) -> list[AcceptOutcome]:
    def work(index: int) -> AcceptOutcome:
        with factory() as session:
            return accept_delivery(session, events[index]).outcome

    return _run_concurrently(len(events), work)


def test_concurrent_same_delivery_id_creates_one_row(session_factory: Factory) -> None:
    outcomes = _accept_all(session_factory, [make_event()] * WORKERS)

    assert Counter(outcomes) == {
        AcceptOutcome.CREATED: 1,
        AcceptOutcome.DUPLICATE_DELIVERY: WORKERS - 1,
    }
    assert _count(session_factory, VcsDelivery) == 1


def test_concurrent_same_revision_different_delivery_ids_creates_one_row(
    session_factory: Factory,
) -> None:
    events = [make_event(delivery_id=f"d{i}") for i in range(WORKERS)]

    outcomes = _accept_all(session_factory, events)

    assert Counter(outcomes) == {
        AcceptOutcome.CREATED: 1,
        AcceptOutcome.DUPLICATE_REVISION: WORKERS - 1,
    }
    assert _count(session_factory, VcsDelivery) == 1


def test_concurrent_workers_on_one_delivery_produce_one_job_and_context(
    session_factory: Factory, clock: FakeClock
) -> None:
    with session_factory() as session:
        delivery_id = accept_delivery(session, make_event()).delivery_id
    clients = [FakeVcsClient() for _ in range(WORKERS)]

    results = _run_concurrently(
        WORKERS,
        lambda i: ingest_delivery(session_factory, clients[i], delivery_id, clock=clock).outcome,
    )

    assert Counter(results) == {
        IngestOutcome.SUCCEEDED: 1,
        IngestOutcome.NOT_CLAIMED: WORKERS - 1,
    }
    assert sum(len(client.calls) for client in clients) == 1
    for model in (Repository, MergeRequest, ReviewJob, ContextPayload):
        assert _count(session_factory, model) == 1


def test_concurrent_accept_and_ingest_of_distinct_prs_share_repository(
    session_factory: Factory, clock: FakeClock
) -> None:
    events = [
        make_event(delivery_id=f"d{i}", pr_number=i + 1, head_sha=f"{i:040x}")
        for i in range(WORKERS)
    ]

    def work(index: int) -> IngestOutcome:
        with session_factory() as session:
            delivery_id = accept_delivery(session, events[index]).delivery_id
        client = FakeVcsClient(
            _snapshot_for(events[index]),
        )
        return ingest_delivery(session_factory, client, delivery_id, clock=clock).outcome

    results = _run_concurrently(WORKERS, work)

    assert results == [IngestOutcome.SUCCEEDED] * WORKERS
    assert _count(session_factory, Repository) == 1
    assert _count(session_factory, MergeRequest) == WORKERS
    assert _count(session_factory, ReviewJob) == WORKERS
    assert _count(session_factory, ContextPayload) == WORKERS


def _snapshot_for(event: VcsEvent) -> RevisionSnapshot:
    return make_snapshot(number=event.pr_number, head_sha=event.head_sha)
