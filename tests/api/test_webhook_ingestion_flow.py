"""Signed delivery -> durable acceptance -> burst worker -> persisted Context Level 1."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from redis import Redis
from rq import SimpleWorker
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContextPayload, ReviewJob, VcsDelivery, VcsDeliveryStatus
from app.queue import get_queue
from app.vcs import GitHubClient, GitLabClient, PullRequestRef, RevisionSnapshot
from app.worker import ingestion as ingestion_job
from tests.api.conftest import (
    Api,
    fixture_bytes,
    github_headers,
    gitlab_signed_headers,
)
from tests.ingestion.conftest import FakeClock

Factory = Callable[[], Session]
HEAD = "a" * 40
BASE = "b" * 40
DIFF = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"
API_BASE = "https://vcs.example.test"


def _github_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/repos/octo/repo/pulls/42"
    if "diff" in request.headers["accept"]:
        return httpx.Response(200, content=DIFF.encode())
    return httpx.Response(
        200,
        json={
            "title": "Add feature",
            "state": "open",
            "head": {"sha": HEAD, "ref": "feature"},
            "base": {"sha": BASE, "ref": "main"},
            "changed_files": 1,
        },
    )


def _gitlab_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path.startswith("/projects/123/merge_requests/7")
    if request.url.path.endswith("/raw_diffs"):
        return httpx.Response(200, content=DIFF.encode())
    return httpx.Response(
        200,
        json={
            "title": "Fix bug",
            "state": "opened",
            "sha": HEAD,
            "source_branch": "fix",
            "target_branch": "main",
            "changes_count": "1",
            "diff_refs": {"base_sha": BASE, "head_sha": HEAD, "start_sha": BASE},
        },
    )


class ProviderClient:
    """Dispatch by provider to the real adapters over ``httpx.MockTransport``."""

    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
        github = GitHubClient(
            API_BASE,
            "gh-token",
            client=httpx.Client(transport=httpx.MockTransport(_github_handler)),
        )
        gitlab = GitLabClient(
            API_BASE,
            "gl-token",
            client=httpx.Client(transport=httpx.MockTransport(_gitlab_handler)),
        )
        return (github if ref.provider == "github" else gitlab).fetch_revision(ref)


@pytest.fixture()
def flow(api: Api, session_factory: Factory, redis_server: Redis, monkeypatch: pytest.MonkeyPatch):
    api.use_factory(session_factory)
    api.use_queue(get_queue(redis_server))
    clock = FakeClock()
    monkeypatch.setattr(
        ingestion_job,
        "_runtime",
        lambda: ingestion_job.Runtime(session_factory, ProviderClient(), clock),
    )
    return api


def _run_worker(api: Api) -> None:
    SimpleWorker([api.queue], connection=api.queue.connection).work(burst=True)


def _post(api: Api, provider: str) -> Any:
    if provider == "github":
        body = fixture_bytes("github_pull_request_opened.json")
        return api.client.post("/webhooks/github", content=body, headers=github_headers(body))
    body = fixture_bytes("gitlab_merge_request_open.json")
    return api.client.post("/webhooks/gitlab", content=body, headers=gitlab_signed_headers(body))


@pytest.mark.parametrize(
    ("provider", "repository", "metadata_title"),
    [("github", "octo/repo", "Add feature"), ("gitlab", "123", "Fix bug")],
)
def test_signed_delivery_yields_persisted_context_level_one(
    flow: Api, session_factory: Factory, provider: str, repository: str, metadata_title: str
) -> None:
    assert _post(flow, provider).json() == {"status": "accepted"}

    _run_worker(flow)

    with session_factory() as session:
        delivery = session.scalars(select(VcsDelivery)).one()
        job = session.scalars(select(ReviewJob)).one()
        payload = session.scalars(select(ContextPayload)).one()
    assert delivery.status == VcsDeliveryStatus.SUCCEEDED
    assert (job.head_sha, job.base_sha) == (HEAD, BASE)
    assert payload.review_job_id == job.id
    assert payload.diff_text == DIFF
    assert (payload.head_sha, payload.base_sha) == (HEAD, BASE)
    assert payload.structured_context is not None
    [file] = payload.structured_context["files"]
    assert (file["new_path"], file["change_type"]) == ("f.py", "modification")
    [hunk] = file["hunks"]
    assert [(line["kind"], line["content"]) for line in hunk["lines"]] == [
        ("context", "keep"),
        ("deletion", "old"),
        ("addition", "new"),
    ]
    assert payload.provider_metadata is not None
    assert payload.provider_metadata["provider"] == provider
    assert payload.provider_metadata["repository"] == repository
    assert payload.provider_metadata["title"] == metadata_title


def test_redelivery_after_processing_creates_no_second_context(
    flow: Api, session_factory: Factory
) -> None:
    _post(flow, "github")
    _run_worker(flow)

    assert _post(flow, "github").json() == {"status": "duplicate"}
    _run_worker(flow)

    with session_factory() as session:
        assert len(session.scalars(select(ReviewJob)).all()) == 1
        assert len(session.scalars(select(ContextPayload)).all()) == 1
