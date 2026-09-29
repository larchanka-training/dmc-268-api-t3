import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import MergeRequest, Repository


def _make_repository() -> Repository:
    return Repository(
        external_id="gitlab:123", name="demo-repo", url="https://gitlab.example.com/demo-repo"
    )


def test_merge_request_links_to_repository(db_session):
    repository = _make_repository()
    db_session.add(repository)
    db_session.flush()

    merge_request = MergeRequest(
        repository_id=repository.id,
        external_id="42",
        title="Add feature",
        source_branch="feature/x",
        target_branch="main",
        author="alice",
    )
    db_session.add(merge_request)
    db_session.commit()

    assert merge_request.repository is repository
    assert repository.merge_requests == [merge_request]


def test_merge_request_requires_existing_repository(db_session):
    merge_request = MergeRequest(
        repository_id=uuid.uuid4(),
        external_id="99",
        title="Orphan MR",
        source_branch="feature/y",
        target_branch="main",
        author="bob",
    )
    db_session.add(merge_request)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_deleting_repository_cascades_to_merge_requests(db_session):
    repository = _make_repository()
    db_session.add(repository)
    db_session.flush()
    db_session.add(
        MergeRequest(
            repository_id=repository.id,
            external_id="1",
            title="MR",
            source_branch="feature",
            target_branch="main",
            author="alice",
        )
    )
    db_session.commit()

    db_session.delete(repository)
    db_session.commit()

    assert db_session.query(MergeRequest).count() == 0
