import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fakeredis import FakeRedis, FakeServer
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion import accept_delivery, ingest_delivery
from app.ingestion.dispatch import DispatchOutcome, dispatch_pending, enqueue_delivery
from app.ingestion.service import DEFAULT_MAX_ATTEMPTS
from app.models import ContextPayload, ReviewJob, VcsDelivery, VcsDeliveryStatus
from app.queue import get_queue
from app.vcs import PullRequestRef, RevisionSnapshot, VcsUpstreamError
from app.worker import ingestion as ingestion_job
from app.worker.recovery import run_loop
from tests.ingestion.conftest import FakeClock, FakeVcsClient, make_event, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
Factory = Callable[[], Session]
STALE = timedelta(minutes=5)


@pytest.fixture()
def queue(redis_server: Redis) -> Queue:
    return get_queue(redis_server)


@pytest.fixture()
def use_runtime(
    monkeypatch: pytest.MonkeyPatch, session_factory: Factory, clock: FakeClock
) -> Callable[[FakeVcsClient], None]:
    def install(client: FakeVcsClient) -> None:
        monkeypatch.setattr(
            ingestion_job,
            "_runtime",
            lambda: ingestion_job.Runtime(session_factory, client, clock),
        )

    return install


def _work(queue: Queue) -> None:
    SimpleWorker([queue], connection=queue.connection).work(burst=True)


def _accept(factory: Factory) -> uuid.UUID:
    with factory() as session:
        return accept_delivery(session, make_event()).delivery_id


def _count(factory: Factory, model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _status(factory: Factory, delivery_id: uuid.UUID) -> VcsDeliveryStatus:
    with factory() as session:
        return session.get_one(VcsDelivery, delivery_id).status


def test_modules_import_without_database_redis_or_network() -> None:
    env = {
        "PATH": os.environ["PATH"],
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "POSTGRES_HOST": "invalid.invalid",
    }
    code = (
        "import app.main, app.api.webhooks, "
        "app.worker.ingestion, app.worker.recovery, app.ingestion.dispatch"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True, cwd=ROOT, timeout=60)


@pytest.mark.parametrize(
    ("provider", "client_name", "base_url", "token"),
    [
        ("github", "GitHubClient", "https://gh.example/api", "gh-token"),
        ("gitlab", "GitLabClient", "https://gl.example/api/v4", "gl-token"),
    ],
)
def test_settings_client_builds_the_provider_client_from_settings(
    monkeypatch: pytest.MonkeyPatch, provider: str, client_name: str, base_url: str, token: str
) -> None:
    built: list[tuple[str, str]] = []

    class Recorder:
        def __init__(self, url: str, secret: str, **_: object) -> None:
            built.append((url, secret))

        def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
            return make_snapshot(number=ref.number)

    monkeypatch.setattr(ingestion_job, client_name, Recorder)
    monkeypatch.setattr(
        ingestion_job,
        "settings",
        replace(
            settings,
            github_api_base_url="https://gh.example/api",
            github_token="gh-token",
            gitlab_api_base_url="https://gl.example/api/v4",
            gitlab_token="gl-token",
        ),
    )
    ref = PullRequestRef(provider=provider, repository="r", number=7, expected_head_sha="a" * 40)

    assert ingestion_job.SettingsVcsClient().fetch_revision(ref).number == 7
    assert built == [(base_url, token)]


def test_unknown_provider_is_rejected_when_fetching() -> None:
    client = ingestion_job.SettingsVcsClient()
    ref = PullRequestRef(provider="bitbucket", repository="r", number=1, expected_head_sha="a" * 40)
    with pytest.raises(ValueError, match="bitbucket"):
        client.fetch_revision(ref)


def test_queue_outage_then_recovery_completes_ingestion(
    session_factory: Factory,
    clock: FakeClock,
    use_runtime: Callable[[FakeVcsClient], None],
) -> None:
    server = FakeServer()
    queue = get_queue(FakeRedis(server=server))
    delivery_id = _accept(session_factory)

    server.connected = False
    outcome = enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    assert outcome == DispatchOutcome.FAILED
    assert dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE) == 0
    assert _status(session_factory, delivery_id) == VcsDeliveryStatus.PENDING

    server.connected = True
    use_runtime(FakeVcsClient())
    assert dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE) == 1
    _work(queue)

    assert _status(session_factory, delivery_id) == VcsDeliveryStatus.SUCCEEDED
    assert _count(session_factory, ReviewJob) == 1


def test_duplicate_dispatch_yields_one_review_job_and_context(
    session_factory: Factory,
    queue: Queue,
    clock: FakeClock,
    use_runtime: Callable[[FakeVcsClient], None],
) -> None:
    delivery_id = _accept(session_factory)
    use_runtime(FakeVcsClient())

    enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    enqueue_delivery(session_factory, delivery_id, queue=queue, clock=clock)
    _work(queue)

    assert _count(session_factory, ReviewJob) == 1
    assert _count(session_factory, ContextPayload) == 1


def test_worker_crash_window_is_recovered_by_dispatcher(
    session_factory: Factory,
    queue: Queue,
    clock: FakeClock,
    use_runtime: Callable[[FakeVcsClient], None],
) -> None:
    delivery_id = _accept(session_factory)
    with pytest.raises(RuntimeError):
        ingest_delivery(
            session_factory, FakeVcsClient(RuntimeError("killed")), delivery_id, clock=clock
        )
    clock.advance(minutes=6)
    use_runtime(FakeVcsClient())

    assert dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE) == 1
    _work(queue)

    assert _status(session_factory, delivery_id) == VcsDeliveryStatus.SUCCEEDED
    assert _count(session_factory, ReviewJob) == 1


def test_retries_are_bounded_and_dispatcher_honors_next_attempt_at(
    session_factory: Factory,
    queue: Queue,
    clock: FakeClock,
    use_runtime: Callable[[FakeVcsClient], None],
) -> None:
    delivery_id = _accept(session_factory)
    use_runtime(FakeVcsClient(VcsUpstreamError("down")))
    dispatches = 0

    for _ in range(DEFAULT_MAX_ATTEMPTS + 3):
        dispatches += dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE)
        _work(queue)
        with session_factory() as session:
            row = session.get_one(VcsDelivery, delivery_id)
            due = row.next_attempt_at
        if due is None:
            break
        clock.advance(seconds=1)
        # Not due yet: the dispatcher must not re-enqueue before next_attempt_at.
        assert dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE) == 0
        clock.now = due

    assert dispatches == DEFAULT_MAX_ATTEMPTS
    with session_factory() as session:
        row = session.get_one(VcsDelivery, delivery_id)
        assert row.status == VcsDeliveryStatus.FAILED
        assert row.attempts == DEFAULT_MAX_ATTEMPTS
    clock.advance(days=1)
    assert dispatch_pending(session_factory, queue, clock=clock, stale_after=STALE) == 0
    assert _count(session_factory, ReviewJob) == 0


def test_recovery_loop_ticks_until_stopped_and_survives_redis_outage(
    session_factory: Factory, clock: FakeClock
) -> None:
    server = FakeServer()
    queue = get_queue(FakeRedis(server=server))
    delivery_id = _accept(session_factory)
    stop = threading.Event()
    ticks = 0

    def wait(_interval: float) -> bool:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            server.connected = True  # Redis comes back after the first failed tick
        if ticks == 2:
            stop.set()
        return stop.is_set()

    server.connected = False
    run_loop(
        session_factory,
        queue,
        stop=stop,
        interval=30,
        stale_after=STALE,
        clock=clock,
        wait=wait,
    )

    assert ticks == 2
    assert queue.job_ids == [f"ingest-{delivery_id}"]
