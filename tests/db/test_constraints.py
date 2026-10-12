import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models import MergeRequest, Repository, ReviewJob, VcsDelivery


@pytest.fixture()
def session(pg_engine: Engine, alembic_config: Config) -> Iterator[Session]:
    command.upgrade(alembic_config, "head")
    alembic_config.attributes["connection"].commit()
    with Session(pg_engine) as db_session:
        yield db_session


def _delivery(**overrides: object) -> VcsDelivery:
    values: dict[str, object] = {
        "provider": "github",
        "delivery_id": "d1",
        "repository_external_id": "github:1",
        "pr_number": 5,
        "action": "opened",
        "head_sha": "a" * 40,
    }
    values.update(overrides)
    return VcsDelivery(**values)


def _merge_request(session: Session, repository: Repository, external_id: str = "5") -> None:
    session.add(
        MergeRequest(
            repository_id=repository.id,
            external_id=external_id,
            title="t",
            source_branch="s",
            target_branch="m",
            author="a",
        )
    )


def test_delivery_defaults(session: Session) -> None:
    delivery = _delivery()
    session.add(delivery)
    session.commit()
    session.refresh(delivery)

    assert delivery.status.value == "pending"
    assert delivery.attempts == 0
    assert delivery.created_at is not None
    assert delivery.base_sha is None


def test_duplicate_provider_delivery_id_rejected(session: Session) -> None:
    session.add(_delivery())
    session.commit()
    # Different revision, same (provider, delivery_id).
    session.add(_delivery(head_sha="b" * 40))
    with pytest.raises(IntegrityError, match="uq_vcs_deliveries_provider_delivery"):
        session.commit()


def test_duplicate_revision_rejected(session: Session) -> None:
    session.add(_delivery())
    session.commit()
    # Different delivery id, same (provider, repository, pr, head_sha).
    session.add(_delivery(delivery_id="d2"))
    with pytest.raises(IntegrityError, match="uq_vcs_deliveries_revision"):
        session.commit()


def test_same_delivery_id_allowed_for_other_provider(session: Session) -> None:
    session.add(_delivery())
    session.add(_delivery(provider="gitlab", repository_external_id="gitlab:1"))
    session.commit()


def test_duplicate_merge_request_per_repository_rejected(session: Session) -> None:
    repository = Repository(external_id=f"github:{uuid.uuid4()}", name="n", url="u")
    session.add(repository)
    session.flush()
    _merge_request(session, repository)
    session.commit()

    _merge_request(session, repository)
    with pytest.raises(IntegrityError, match="uq_merge_requests_repository_external_id"):
        session.commit()


def test_duplicate_review_job_head_rejected(session: Session) -> None:
    repository = Repository(external_id=f"github:{uuid.uuid4()}", name="n", url="u")
    session.add(repository)
    session.flush()
    merge_request = MergeRequest(
        repository_id=repository.id,
        external_id="5",
        title="t",
        source_branch="s",
        target_branch="m",
        author="a",
    )
    session.add(merge_request)
    session.flush()
    session.add(ReviewJob(merge_request_id=merge_request.id, head_sha="a" * 40))
    session.commit()

    session.add(ReviewJob(merge_request_id=merge_request.id, head_sha="a" * 40))
    with pytest.raises(IntegrityError, match="uq_review_jobs_merge_request_head"):
        session.commit()
