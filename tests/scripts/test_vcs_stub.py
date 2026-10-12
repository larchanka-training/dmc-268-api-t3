"""Contract: the real VCS adapters accept what the CI stub upstream serves."""

import threading
from collections.abc import Iterator

import httpx
import pytest

from app.context import parse_diff
from app.vcs.errors import VcsNotFoundError
from app.vcs.github import GitHubClient
from app.vcs.gitlab import GitLabClient
from app.vcs.models import PullRequestRef
from scripts.vcs_stub import make_server

HEAD = "a" * 40
BASE = "b" * 40


@pytest.fixture(name="base_url")
def base_url_fixture() -> Iterator[str]:
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_github_adapter_fetches_fixture_revision(base_url: str) -> None:
    snapshot = GitHubClient(base_url, "t").fetch_revision(
        PullRequestRef("github", "octo/repo", 42, HEAD)
    )

    assert (snapshot.head_sha, snapshot.base_sha) == (HEAD, BASE)
    assert len(parse_diff(snapshot.raw_diff).files) == 1


def test_gitlab_adapter_fetches_fixture_revision(base_url: str) -> None:
    snapshot = GitLabClient(f"{base_url}/api/v4", "t").fetch_revision(
        PullRequestRef("gitlab", "123", 7, HEAD)
    )

    assert (snapshot.head_sha, snapshot.base_sha) == (HEAD, BASE)
    assert len(parse_diff(snapshot.raw_diff).files) == 1


def test_unknown_pull_request_is_not_found(base_url: str) -> None:
    client = GitHubClient(base_url, "t")

    with pytest.raises(VcsNotFoundError):
        client.fetch_revision(PullRequestRef("github", "other/repo", 1, HEAD))


def test_unknown_path_returns_404(base_url: str) -> None:
    assert httpx.get(f"{base_url}/nope").status_code == 404
