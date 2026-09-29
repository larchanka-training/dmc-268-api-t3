import pytest
from sqlalchemy.exc import StatementError

from app.models import MergeRequest, Repository, ReviewJob
from app.models.review_job import ReviewJobStatus


def _make_merge_request(db_session) -> MergeRequest:
    repository = Repository(external_id="r1", name="repo", url="https://example.com/repo")
    db_session.add(repository)
    db_session.flush()
    merge_request = MergeRequest(
        repository_id=repository.id,
        external_id="1",
        title="MR",
        source_branch="feature",
        target_branch="main",
        author="alice",
    )
    db_session.add(merge_request)
    db_session.flush()
    return merge_request


def test_review_job_defaults_to_pending(db_session):
    merge_request = _make_merge_request(db_session)

    review_job = ReviewJob(merge_request_id=merge_request.id)
    db_session.add(review_job)
    db_session.commit()

    assert review_job.status == ReviewJobStatus.PENDING
    assert merge_request.review_jobs == [review_job]


def test_review_job_rejects_invalid_status(db_session):
    merge_request = _make_merge_request(db_session)

    review_job = ReviewJob(merge_request_id=merge_request.id, status="not-a-status")
    db_session.add(review_job)

    # SQLAlchemy wraps the LookupError raised by the Enum type's validation
    # in a StatementError at flush time; the original is available as __cause__.
    with pytest.raises(StatementError) as exc_info:
        db_session.commit()
    assert isinstance(exc_info.value.__cause__, LookupError)
