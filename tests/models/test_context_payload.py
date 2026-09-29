import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ContextPayload, MergeRequest, Repository, ReviewJob


def _make_review_job(db_session) -> ReviewJob:
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
    review_job = ReviewJob(merge_request_id=merge_request.id)
    db_session.add(review_job)
    db_session.flush()
    return review_job


def test_context_payload_links_to_review_job(db_session):
    review_job = _make_review_job(db_session)

    context_payload = ContextPayload(
        review_job_id=review_job.id, diff_text="diff --git a b", language="python"
    )
    db_session.add(context_payload)
    db_session.commit()

    assert review_job.context_payload is context_payload


def test_context_payload_is_unique_per_review_job(db_session):
    review_job = _make_review_job(db_session)
    db_session.add(ContextPayload(review_job_id=review_job.id, diff_text="first"))
    db_session.commit()

    db_session.add(ContextPayload(review_job_id=review_job.id, diff_text="second"))

    with pytest.raises(IntegrityError):
        db_session.commit()
