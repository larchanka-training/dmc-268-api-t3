import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.context import DiffParseError
from app.ingestion import IngestOutcome, VcsEvent, accept_delivery, ingest_delivery, service
from app.models import (
    ContextPayload,
    MergeRequest,
    Repository,
    ReviewJob,
    VcsDelivery,
    VcsDeliveryStatus,
)
from app.models.review_job import ReviewJobStatus
from app.vcs import (
    VcsAuthError,
    VcsDiffIncompleteError,
    VcsDiffTooLargeError,
    VcsNotFoundError,
    VcsRateLimitedError,
    VcsRevisionChangedError,
    VcsUpstreamError,
)
from app.worker import ingestion as worker_ingestion
from app.worker.ingestion import SettingsVcsClient
from tests.ingestion.conftest import DIFF, HEAD, FakeClock, FakeVcsClient, make_event, make_snapshot

Factory = Callable[[], Session]


def _accept(factory: Factory, event: VcsEvent | None = None) -> uuid.UUID:
    with factory() as session:
        return accept_delivery(session, event or make_event()).delivery_id


def _count(factory: Factory, model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _delivery(factory: Factory, delivery_id: uuid.UUID) -> VcsDelivery:
    with factory() as session:
        return session.get_one(VcsDelivery, delivery_id)


def _wait(factory: Factory, delivery_id: uuid.UUID, clock: FakeClock) -> timedelta:
    next_attempt_at = _delivery(factory, delivery_id).next_attempt_at
    assert next_attempt_at is not None
    return next_attempt_at - clock()


def test_success_creates_job_and_context_atomically(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient()

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.SUCCEEDED
    assert len(client.calls) == 1
    assert (client.calls[0].provider, client.calls[0].repository) == ("github", "octo/repo")
    assert (client.calls[0].number, client.calls[0].expected_head_sha) == (5, HEAD)
    with session_factory() as session:
        repository = session.scalars(select(Repository)).one()
        assert repository.external_id == "github:octo/repo"
        merge_request = session.scalars(select(MergeRequest)).one()
        assert merge_request.repository_id == repository.id
        assert merge_request.external_id == "5"
        assert merge_request.title == "Add feature"
        job = session.scalars(select(ReviewJob)).one()
        assert job.id == result.review_job_id
        assert job.status is ReviewJobStatus.PENDING
        assert job.head_sha == HEAD
        context = session.scalars(select(ContextPayload)).one()
        assert context.review_job_id == job.id
        assert context.diff_text == DIFF
        assert context.head_sha == HEAD
        assert context.schema_version == 1
        assert context.structured_context is not None
        assert context.structured_context["files"][0]["new_path"] == "f.py"
        assert context.provider_metadata is not None
        assert context.provider_metadata["delivery_id"] == "d1"
        assert context.provider_metadata["head_ref"] == "feature"
        row = session.get_one(VcsDelivery, delivery_id)
        assert row.status is VcsDeliveryStatus.SUCCEEDED
        assert row.attempts == 1
        assert row.locked_until is None
        assert row.last_error is None


def test_repository_and_merge_request_are_upserted(
    session_factory: Factory, clock: FakeClock
) -> None:
    first = _accept(session_factory)
    second = _accept(session_factory, make_event(delivery_id="d2", head_sha="c" * 40))
    ingest_delivery(session_factory, FakeVcsClient(), first, clock=clock)
    ingest_delivery(
        session_factory,
        FakeVcsClient(make_snapshot(head_sha="c" * 40, title="Renamed")),
        second,
        clock=clock,
    )

    assert _count(session_factory, Repository) == 1
    assert _count(session_factory, MergeRequest) == 1
    assert _count(session_factory, ReviewJob) == 2
    with session_factory() as session:
        assert session.scalars(select(MergeRequest)).one().title == "Renamed"


def test_empty_diff_is_valid(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(make_snapshot(raw_diff=""))

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.SUCCEEDED
    with session_factory() as session:
        context = session.scalars(select(ContextPayload)).one()
        assert context.diff_text == ""
        assert context.structured_context == {"schema_version": 1, "files": []}


def test_deterministic_write_failure_is_terminal_and_leaves_no_partial_rows(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    # base_sha exceeds VARCHAR(64): the job insert fails after repository and MR were written.
    client = FakeVcsClient(make_snapshot(base_sha="b" * 100))

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    for model in (Repository, MergeRequest, ReviewJob, ContextPayload):
        assert _count(session_factory, model) == 0
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.locked_until, row.next_attempt_at) == (
        VcsDeliveryStatus.FAILED,
        None,
        None,
    )
    assert row.last_error is not None
    assert row.last_error.startswith("DataError: ")
    # The driver message is kept; the SQL and bound parameters are not.
    assert "[SQL:" not in row.last_error
    assert "b" * 100 not in row.last_error


def test_transient_write_failure_stays_on_the_lease_path(
    session_factory: Factory, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery_id = _accept(session_factory)

    def lost_connection(*_: object) -> None:
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(service, "_upsert_repository", lost_connection)

    with pytest.raises(OperationalError):
        ingest_delivery(session_factory, FakeVcsClient(), delivery_id, clock=clock)

    assert _delivery(session_factory, delivery_id).status is VcsDeliveryStatus.PROCESSING


def test_unknown_provider_fails_terminally_with_a_recorded_cause(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory, make_event(provider="bitbucket"))

    result = ingest_delivery(session_factory, SettingsVcsClient(), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.attempts, row.locked_until) == (VcsDeliveryStatus.FAILED, 1, None)
    assert row.last_error is not None
    assert "bitbucket" in row.last_error
    assert _count(session_factory, ReviewJob) == 0


def test_malformed_repository_identity_fails_terminally(
    session_factory: Factory, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker_ingestion,
        "settings",
        replace(settings, github_api_base_url="https://gh.example/api", github_token="t0ken-value"),
    )
    delivery_id = _accept(session_factory, make_event(repository_external_id="no-slash"))

    result = ingest_delivery(session_factory, SettingsVcsClient(), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    row = _delivery(session_factory, delivery_id)
    assert row.status is VcsDeliveryStatus.FAILED
    assert row.last_error is not None
    assert "owner/name" in row.last_error


def test_missing_token_fails_terminally(
    session_factory: Factory, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker_ingestion,
        "settings",
        replace(settings, github_api_base_url="https://gh.example/api", github_token=""),
    )
    delivery_id = _accept(session_factory)

    result = ingest_delivery(session_factory, SettingsVcsClient(), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    row = _delivery(session_factory, delivery_id)
    assert row.status is VcsDeliveryStatus.FAILED
    assert row.last_error is not None
    assert "token" in row.last_error


def test_already_succeeded_delivery_is_not_reprocessed(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    ingest_delivery(session_factory, FakeVcsClient(), delivery_id, clock=clock)
    client = FakeVcsClient()

    clock.advance(days=1)
    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.NOT_CLAIMED
    assert client.calls == []
    assert _count(session_factory, ReviewJob) == 1


def test_unknown_delivery_is_not_claimed(session_factory: Factory, clock: FakeClock) -> None:
    result = ingest_delivery(session_factory, FakeVcsClient(), uuid.uuid4(), clock=clock)
    assert result.outcome is IngestOutcome.NOT_CLAIMED


# --- failure policy -------------------------------------------------------

RETRYABLE: list[Exception] = [
    VcsRateLimitedError("slow down"),
    VcsUpstreamError("502"),
    VcsRevisionChangedError("moved"),
    VcsDiffIncompleteError("still computing"),
]
TERMINAL: list[Exception] = [
    VcsAuthError("bad credentials"),
    VcsNotFoundError("gone"),
    VcsDiffTooLargeError("too big"),
    VcsUpstreamError("400", retryable=False),
]


@pytest.mark.parametrize("error", RETRYABLE, ids=lambda e: type(e).__name__)
def test_retryable_error_schedules_retry_with_backoff(
    session_factory: Factory, clock: FakeClock, error: Exception
) -> None:
    delivery_id = _accept(session_factory)

    result = ingest_delivery(session_factory, FakeVcsClient(error), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.RETRY_SCHEDULED
    row = _delivery(session_factory, delivery_id)
    assert row.status is VcsDeliveryStatus.PENDING
    assert row.attempts == 1
    assert row.locked_until is None
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > clock()
    assert row.last_error is not None
    assert row.last_error.startswith(f"{type(error).__name__}: ")
    assert _count(session_factory, ReviewJob) == 0
    assert _count(session_factory, ContextPayload) == 0


def test_backoff_grows_exponentially_and_waits_for_next_attempt_at(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(VcsUpstreamError("502"))

    ingest_delivery(session_factory, client, delivery_id, clock=clock)
    first_wait = _wait(session_factory, delivery_id, clock)
    # Not yet due: not claimed, no new fetch.
    early = ingest_delivery(session_factory, client, delivery_id, clock=clock)
    assert early.outcome is IngestOutcome.NOT_CLAIMED
    assert len(client.calls) == 1

    clock.advance(seconds=first_wait.total_seconds())
    ingest_delivery(session_factory, client, delivery_id, clock=clock)
    second_wait = _wait(session_factory, delivery_id, clock)

    assert second_wait == first_wait * 2
    assert len(client.calls) == 2


def test_retry_after_hint_extends_wait(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)
    error = VcsRateLimitedError("slow down", retry_after=900)

    ingest_delivery(session_factory, FakeVcsClient(error), delivery_id, clock=clock)

    wait = _wait(session_factory, delivery_id, clock)
    assert wait >= timedelta(seconds=900)


def test_retry_then_success_creates_one_job(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(VcsUpstreamError("502"), make_snapshot())

    ingest_delivery(session_factory, client, delivery_id, clock=clock)
    clock.advance(hours=2)
    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.SUCCEEDED
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.attempts, row.last_error) == (VcsDeliveryStatus.SUCCEEDED, 2, None)
    assert _count(session_factory, ReviewJob) == 1


def test_retries_exhaust_to_failed(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(VcsUpstreamError("502"))

    outcomes = []
    for _ in range(5):
        outcomes.append(ingest_delivery(session_factory, client, delivery_id, clock=clock).outcome)
        clock.advance(days=1)

    assert outcomes == [IngestOutcome.RETRY_SCHEDULED] * 4 + [IngestOutcome.FAILED]
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.attempts) == (VcsDeliveryStatus.FAILED, 5)
    assert row.last_error is not None
    assert row.last_error.startswith("VcsUpstreamError: ")
    assert _count(session_factory, ReviewJob) == 0
    later = ingest_delivery(session_factory, client, delivery_id, clock=clock)
    assert later.outcome is IngestOutcome.NOT_CLAIMED


def test_max_attempts_is_configurable(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)

    result = ingest_delivery(
        session_factory,
        FakeVcsClient(VcsUpstreamError("502")),
        delivery_id,
        clock=clock,
        max_attempts=1,
    )

    assert result.outcome is IngestOutcome.FAILED


@pytest.mark.parametrize("error", TERMINAL, ids=lambda e: type(e).__name__)
def test_terminal_error_fails_immediately(
    session_factory: Factory, clock: FakeClock, error: Exception
) -> None:
    delivery_id = _accept(session_factory)

    result = ingest_delivery(session_factory, FakeVcsClient(error), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.attempts, row.locked_until) == (VcsDeliveryStatus.FAILED, 1, None)
    assert row.last_error is not None
    assert row.last_error.startswith(f"{type(error).__name__}: ")
    assert _count(session_factory, ReviewJob) == 0
    assert _count(session_factory, ContextPayload) == 0


def test_parse_error_is_terminal_and_creates_nothing(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(make_snapshot(raw_diff="diff --git a/x b/x\n@@ garbage @@\n"))

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.FAILED
    row = _delivery(session_factory, delivery_id)
    assert row.status is VcsDeliveryStatus.FAILED
    assert row.last_error is not None
    assert row.last_error.startswith(f"{DiffParseError.__name__}: ")
    for model in (Repository, MergeRequest, ReviewJob, ContextPayload):
        assert _count(session_factory, model) == 0


def test_snapshot_for_other_head_is_retried_as_revision_change(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    client = FakeVcsClient(make_snapshot(head_sha="d" * 40))

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.RETRY_SCHEDULED
    row = _delivery(session_factory, delivery_id)
    assert row.last_error is not None
    assert row.last_error.startswith("VcsRevisionChangedError: ")
    assert _count(session_factory, ReviewJob) == 0


def test_last_error_is_redacted_and_bounded(session_factory: Factory, clock: FakeClock) -> None:
    delivery_id = _accept(session_factory)
    secret = "ghp_" + "A" * 36
    message = (
        f"boom Authorization: Bearer {secret} token=abc123 https://user:pw@host/x " + "z" * 5000
    )

    ingest_delivery(session_factory, FakeVcsClient(VcsAuthError(message)), delivery_id, clock=clock)

    error = _delivery(session_factory, delivery_id).last_error
    assert error is not None
    assert secret not in error
    assert "abc123" not in error
    assert "user:pw" not in error
    assert "[REDACTED]" in error
    assert len(error) <= 2000


# --- lease handling -------------------------------------------------------


def test_claimed_row_cannot_be_processed_by_second_worker(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    inner: list[IngestOutcome] = []

    class ReentrantClient(FakeVcsClient):
        def fetch_revision(self, ref):
            # While the first worker is mid-fetch (row claimed), a second worker tries.
            second = FakeVcsClient()
            inner.append(ingest_delivery(session_factory, second, delivery_id, clock=clock).outcome)
            assert second.calls == []
            return super().fetch_revision(ref)

    result = ingest_delivery(session_factory, ReentrantClient(), delivery_id, clock=clock)

    assert inner == [IngestOutcome.NOT_CLAIMED]
    assert result.outcome is IngestOutcome.SUCCEEDED
    assert _count(session_factory, ReviewJob) == 1


def test_expired_lease_is_reclaimable_and_stale_worker_cannot_commit(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    inner: list[IngestOutcome] = []

    class StalledClient(FakeVcsClient):
        def fetch_revision(self, ref):
            # The first worker stalls past its lease; a second worker reclaims and finishes.
            clock.advance(hours=1)
            inner.append(
                ingest_delivery(session_factory, FakeVcsClient(), delivery_id, clock=clock).outcome
            )
            return super().fetch_revision(ref)

    result = ingest_delivery(session_factory, StalledClient(), delivery_id, clock=clock)

    assert inner == [IngestOutcome.SUCCEEDED]
    assert result.outcome is IngestOutcome.LEASE_LOST
    assert _count(session_factory, ReviewJob) == 1
    assert _count(session_factory, ContextPayload) == 1
    row = _delivery(session_factory, delivery_id)
    assert (row.status, row.attempts) == (VcsDeliveryStatus.SUCCEEDED, 2)


def test_crashed_worker_row_is_reclaimed_after_lease_and_counts_attempts(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    with pytest.raises(RuntimeError):
        ingest_delivery(session_factory, _Crashing(), delivery_id, clock=clock)
    assert _delivery(session_factory, delivery_id).status is VcsDeliveryStatus.PROCESSING
    assert (
        ingest_delivery(session_factory, FakeVcsClient(), delivery_id, clock=clock).outcome
        is IngestOutcome.NOT_CLAIMED
    )

    clock.advance(hours=1)
    result = ingest_delivery(session_factory, FakeVcsClient(), delivery_id, clock=clock)

    assert result.outcome is IngestOutcome.SUCCEEDED
    assert _delivery(session_factory, delivery_id).attempts == 2


def test_reclaim_after_exhausted_attempts_fails_the_row(
    session_factory: Factory, clock: FakeClock
) -> None:
    delivery_id = _accept(session_factory)
    with pytest.raises(RuntimeError):
        ingest_delivery(session_factory, _Crashing(), delivery_id, clock=clock, max_attempts=1)
    clock.advance(hours=1)
    client = FakeVcsClient()

    result = ingest_delivery(session_factory, client, delivery_id, clock=clock, max_attempts=1)

    assert result.outcome is IngestOutcome.FAILED
    assert client.calls == []
    row = _delivery(session_factory, delivery_id)
    assert row.status is VcsDeliveryStatus.FAILED
    assert row.last_error is not None
    assert row.last_error.startswith("AttemptsExhausted: ")


class _Crashing(FakeVcsClient):
    def fetch_revision(self, ref):
        raise RuntimeError("worker died")
