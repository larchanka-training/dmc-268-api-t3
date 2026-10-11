from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.ingestion import VcsEvent
from app.vcs import PullRequestRef, RevisionSnapshot
from tests.db.conftest import alembic_config, pg_engine  # noqa: F401  (reused fixtures)

HEAD = "a" * 40
BASE = "b" * 40
DIFF = (
    "diff --git a/f.py b/f.py\n"
    "index 111..222 100644\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,2 +1,2 @@\n"
    " keep\n"
    "-old\n"
    "+new\n"
)


def make_event(**overrides: object) -> VcsEvent:
    event = VcsEvent(
        provider="github",
        delivery_id="d1",
        repository_external_id="octo/repo",
        pr_number=5,
        action="opened",
        head_sha=HEAD,
        base_sha=BASE,
        repository_name="octo/repo",
        repository_url="https://github.com/octo/repo",
        title="Add feature",
        author="octocat",
        source_branch="feature",
        target_branch="main",
    )
    return replace(event, **overrides)  # type: ignore[arg-type]


def make_snapshot(**overrides: object) -> RevisionSnapshot:
    snapshot = RevisionSnapshot(
        provider="github",
        repository="octo/repo",
        number=5,
        title="Add feature",
        state="open",
        head_sha=HEAD,
        base_sha=BASE,
        head_ref="feature",
        base_ref="main",
        raw_diff=DIFF,
    )
    return replace(snapshot, **overrides)  # type: ignore[arg-type]


class FakeVcsClient:
    """Plays back scripted results (snapshot or exception) and records calls."""

    def __init__(self, *results: RevisionSnapshot | Exception) -> None:
        self.results = list(results) or [make_snapshot()]
        self.calls: list[PullRequestRef] = []

    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
        self.calls.append(ref)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def session_factory(
    pg_engine: Engine,  # noqa: F811
    alembic_config: Config,  # noqa: F811
) -> Iterator[Callable[[], Session]]:
    command.upgrade(alembic_config, "head")
    alembic_config.attributes["connection"].commit()
    yield sessionmaker(bind=pg_engine, expire_on_commit=False)
