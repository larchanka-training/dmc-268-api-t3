from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion import AcceptOutcome, accept_delivery
from app.models import ReviewJob, VcsDelivery, VcsDeliveryStatus
from tests.ingestion.conftest import make_event


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_new_delivery_is_created_pending(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        result = accept_delivery(session, make_event())

    assert result.outcome is AcceptOutcome.CREATED
    with session_factory() as session:
        row = session.get(VcsDelivery, result.delivery_id)
        assert row is not None
        assert row.status is VcsDeliveryStatus.PENDING
        assert row.attempts == 0
        assert (row.provider, row.delivery_id, row.pr_number, row.head_sha) == (
            "github",
            "d1",
            5,
            "a" * 40,
        )
        assert row.title == "Add feature"
        assert _count(session, ReviewJob) == 0


def test_same_delivery_id_twice_is_duplicate_delivery(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        first = accept_delivery(session, make_event())
    with session_factory() as session:
        second = accept_delivery(session, make_event())

    assert second.outcome is AcceptOutcome.DUPLICATE_DELIVERY
    assert second.delivery_id == first.delivery_id
    with session_factory() as session:
        assert _count(session, VcsDelivery) == 1


def test_new_delivery_id_same_revision_is_duplicate_revision(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        first = accept_delivery(session, make_event())
    with session_factory() as session:
        second = accept_delivery(session, make_event(delivery_id="d2"))

    assert second.outcome is AcceptOutcome.DUPLICATE_REVISION
    assert second.delivery_id == first.delivery_id
    with session_factory() as session:
        assert _count(session, VcsDelivery) == 1


def test_new_head_sha_is_a_new_revision(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        accept_delivery(session, make_event())
    with session_factory() as session:
        second = accept_delivery(session, make_event(delivery_id="d2", head_sha="c" * 40))

    assert second.outcome is AcceptOutcome.CREATED
    with session_factory() as session:
        assert _count(session, VcsDelivery) == 2


def test_same_delivery_id_other_provider_is_independent(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        accept_delivery(session, make_event())
    with session_factory() as session:
        other = accept_delivery(session, make_event(provider="gitlab", repository_external_id="7"))

    assert other.outcome is AcceptOutcome.CREATED
