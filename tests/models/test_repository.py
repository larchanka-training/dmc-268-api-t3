import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Repository


def test_repository_generates_id_and_created_at(db_session):
    repository = Repository(
        external_id="gitlab:123",
        name="demo-repo",
        url="https://gitlab.example.com/demo-repo",
    )
    db_session.add(repository)
    db_session.commit()

    assert repository.id is not None
    assert repository.created_at is not None


def test_repository_external_id_is_unique(db_session):
    db_session.add(Repository(external_id="dup", name="one", url="https://example.com/one"))
    db_session.commit()

    db_session.add(Repository(external_id="dup", name="two", url="https://example.com/two"))

    with pytest.raises(IntegrityError):
        db_session.commit()
