import pytest
from sqlalchemy.exc import StatementError

from app.models import Finding, MergeRequest, Repository, ReviewJob
from app.models.finding import FindingSeverity


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


def test_finding_links_to_review_job(db_session):
    review_job = _make_review_job(db_session)

    finding = Finding(
        review_job_id=review_job.id,
        file_path="app/main.py",
        line_number=10,
        severity=FindingSeverity.WARNING,
        message="Unused import",
    )
    db_session.add(finding)
    db_session.commit()

    assert review_job.findings == [finding]


def test_multiple_findings_per_review_job(db_session):
    review_job = _make_review_job(db_session)
    db_session.add_all(
        [
            Finding(
                review_job_id=review_job.id,
                file_path="a.py",
                severity=FindingSeverity.INFO,
                message="Note",
            ),
            Finding(
                review_job_id=review_job.id,
                file_path="b.py",
                severity=FindingSeverity.CRITICAL,
                message="Bug",
            ),
        ]
    )
    db_session.commit()

    assert len(review_job.findings) == 2


def test_finding_rejects_invalid_severity(db_session):
    review_job = _make_review_job(db_session)

    finding = Finding(
        review_job_id=review_job.id,
        file_path="a.py",
        severity="not-a-severity",
        message="Bug",
    )
    db_session.add(finding)

    # SQLAlchemy wraps the LookupError raised by the Enum type's validation
    # in a StatementError at flush time; the original is available as __cause__.
    with pytest.raises(StatementError) as exc_info:
        db_session.commit()
    assert isinstance(exc_info.value.__cause__, LookupError)
