from collections.abc import Callable

import httpx
import pytest

from app.llm.tokens import approximate_token_count
from app.vcs import (
    GitHubClient,
    PullRequestRef,
    VcsAuthError,
    VcsDiffIncompleteError,
    VcsDiffTooLargeError,
    VcsError,
    VcsNotFoundError,
    VcsRateLimitedError,
    VcsRevisionChangedError,
    VcsUpstreamError,
)

BASE = "https://api.example.test"
TOKEN = "ghp_supersecret"
HEAD = "a" * 40
BASE_SHA = "b" * 40
DIFF = (
    "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+diff --git fake\n"
)
REF = PullRequestRef(provider="github", repository="acme/widgets", number=7, expected_head_sha=HEAD)


def meta(head: str = HEAD, base: str = BASE_SHA, **extra: object) -> dict[str, object]:
    return {
        "title": "Add thing",
        "state": "open",
        "head": {"sha": head, "ref": "feature"},
        "base": {"sha": base, "ref": "main"},
        "changed_files": 2,
        **extra,
    }


Handler = Callable[[httpx.Request], httpx.Response]


def is_diff(request: httpx.Request) -> bool:
    return "diff" in request.headers["accept"]


def make(handler: Handler, **kwargs: object) -> tuple[GitHubClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording))
    return GitHubClient(BASE, TOKEN, client=client, **kwargs), seen  # type: ignore[arg-type]


def ok_handler(diff: bytes | str = DIFF, **meta_kwargs: object) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if is_diff(request):
            return httpx.Response(200, content=diff if isinstance(diff, bytes) else diff.encode())
        return httpx.Response(200, json=meta(**meta_kwargs))  # type: ignore[arg-type]

    return handler


def test_fetches_metadata_diff_metadata_with_headers():
    client, seen = make(ok_handler())

    snap = client.fetch_revision(REF)

    assert snap.raw_diff == DIFF
    assert (snap.head_sha, snap.base_sha) == (HEAD, BASE_SHA)
    assert (snap.title, snap.state, snap.head_ref, snap.base_ref) == (
        "Add thing",
        "open",
        "feature",
        "main",
    )
    assert [is_diff(r) for r in seen] == [False, True, False]
    for request in seen:
        assert str(request.url) == f"{BASE}/repos/acme/widgets/pulls/7"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["x-github-api-version"] == "2022-11-28"
    assert seen[1].headers["accept"] == "application/vnd.github.diff"


def test_identifiers_are_url_quoted_and_validated():
    client, seen = make(ok_handler())
    ref = PullRequestRef("github", "a b/c%d", 1, HEAD)
    client.fetch_revision(ref)
    assert seen[0].url.raw_path.decode().startswith("/repos/a%20b/c%25d/pulls/1")
    for bad in ("../x", "a/b/c", "a", "/b", "a/.."):
        with pytest.raises(ValueError):
            client.fetch_revision(PullRequestRef("github", bad, 1, HEAD))


def test_redirect_is_not_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.test/x"})

    client, seen = make(handler)
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable
    assert len(seen) == 1


def test_expected_head_mismatch_raises_before_diff():
    client, seen = make(ok_handler(head="c" * 40))
    with pytest.raises(VcsRevisionChangedError):
        client.fetch_revision(REF)
    assert len(seen) == 1


def test_head_or_base_changing_during_fetch_raises():
    for changed in ({"head": "c" * 40}, {"base": "d" * 40}):
        calls = {"meta": 0}

        def handler(
            request: httpx.Request,
            changed: dict[str, str] = changed,
            calls: dict[str, int] = calls,
        ) -> httpx.Response:
            if is_diff(request):
                return httpx.Response(200, text=DIFF)
            calls["meta"] += 1
            if calls["meta"] == 1:
                return httpx.Response(200, json=meta())
            return httpx.Response(200, json=meta(**changed))

        client, _ = make(handler)
        with pytest.raises(VcsRevisionChangedError) as info:
            client.fetch_revision(REF)
        assert not info.value.retryable


@pytest.mark.parametrize(
    ("status", "headers", "body", "error", "retryable"),
    [
        (401, {}, {}, VcsAuthError, False),
        (403, {}, {"message": "Forbidden"}, VcsAuthError, False),
        (404, {}, {}, VcsNotFoundError, False),
        (429, {"retry-after": "12"}, {}, VcsRateLimitedError, True),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"},
            {},
            VcsRateLimitedError,
            True,
        ),
        (403, {"retry-after": "60"}, {}, VcsRateLimitedError, True),
        (
            403,
            {},
            {"message": "You have exceeded a secondary rate limit."},
            VcsRateLimitedError,
            True,
        ),
        (500, {}, {}, VcsUpstreamError, True),
        (503, {}, {}, VcsUpstreamError, True),
        (418, {}, {}, VcsUpstreamError, False),
    ],
)
def test_status_mapping_on_metadata(status, headers, body, error, retryable):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json=body)

    client, seen = make(handler, clock=lambda: 1000.0)
    with pytest.raises(error) as info:
        client.fetch_revision(REF)
    assert type(info.value) is error
    assert info.value.retryable is retryable
    assert len(seen) == 1  # no internal retries
    assert TOKEN not in str(info.value)


def test_rate_limit_retry_after_values():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}
        )

    client, _ = make(handler, clock=lambda: 1000.0)
    with pytest.raises(VcsRateLimitedError) as info:
        client.fetch_revision(REF)
    assert info.value.retry_after == 30.0


def test_errors_on_diff_request_are_mapped():
    def handler(status: int) -> Handler:
        def inner(request: httpx.Request) -> httpx.Response:
            if is_diff(request):
                return httpx.Response(status, json={"message": "x"})
            return httpx.Response(200, json=meta())

        return inner

    client, _ = make(handler(404))
    with pytest.raises(VcsNotFoundError):
        client.fetch_revision(REF)
    for status in (406, 422):
        client, _ = make(handler(status))
        with pytest.raises(VcsDiffIncompleteError) as info:
            client.fetch_revision(REF)
        assert not info.value.retryable


def test_second_metadata_read_error_is_surfaced():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_diff(request):
            return httpx.Response(200, text=DIFF)
        calls["n"] += 1
        return httpx.Response(200, json=meta()) if calls["n"] == 1 else httpx.Response(502)

    client, _ = make(handler)
    with pytest.raises(VcsUpstreamError):
        client.fetch_revision(REF)


def test_timeout_and_transport_errors_are_retryable_upstream():
    for exc in (httpx.ReadTimeout("t"), httpx.ConnectError("c")):

        def handler(request: httpx.Request, exc: Exception = exc) -> httpx.Response:
            raise exc

        client, _ = make(handler)
        with pytest.raises(VcsUpstreamError) as info:
            client.fetch_revision(REF)
        assert info.value.retryable
        assert TOKEN not in str(info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"head": {}, "base": {"sha": "x"}}),
        httpx.Response(200, json={"head": {"sha": 1}, "base": {"sha": "x"}}),
    ],
)
def test_malformed_metadata_is_upstream_error(response):
    client, _ = make(lambda request: response)
    with pytest.raises(VcsUpstreamError):
        client.fetch_revision(REF)


def test_empty_diff_is_valid():
    client, _ = make(ok_handler(diff="", changed_files=0))
    assert client.fetch_revision(REF).raw_diff == ""


def test_non_utf8_diff_is_error():
    client, _ = make(ok_handler(diff=b"\xff\xfe\x00bad", changed_files=0))
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable


def test_file_count_mismatch_is_incomplete():
    client, _ = make(ok_handler(changed_files=3))
    with pytest.raises(VcsDiffIncompleteError):
        client.fetch_revision(REF)


def _body_of_tokens(tokens: int, *, multibyte: bool = False) -> str:
    char = "é" if multibyte else "a"
    text = char * (tokens * 4)
    assert approximate_token_count(text) == tokens
    return text


def test_diff_at_limit_is_accepted_and_just_over_is_rejected():
    at_limit = _body_of_tokens(32000)
    client, _ = make(ok_handler(diff=at_limit, changed_files=None))
    assert client.fetch_revision(REF).raw_diff == at_limit

    over = at_limit + "aaaa"
    client, _ = make(ok_handler(diff=over, changed_files=None))
    with pytest.raises(VcsDiffTooLargeError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable


def test_multibyte_diff_within_token_limit_is_accepted():
    text = _body_of_tokens(32000, multibyte=True)  # 128000 chars, 256000 bytes
    client, _ = make(ok_handler(diff=text, changed_files=None))
    assert client.fetch_revision(REF).raw_diff == text


def test_chunked_stream_over_byte_ceiling_is_rejected_without_full_read():
    produced = {"bytes": 0}

    def chunks():
        for _ in range(1000):
            produced["bytes"] += 10_000
            yield b"a" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        if is_diff(request):
            return httpx.Response(200, content=chunks())
        return httpx.Response(200, json=meta())

    client, _ = make(handler, max_diff_tokens=1000)  # ceiling 16,000 bytes
    with pytest.raises(VcsDiffTooLargeError):
        client.fetch_revision(REF)
    assert produced["bytes"] < 100_000


def test_custom_token_limit_applies():
    client, _ = make(ok_handler(diff="a" * 40, changed_files=None), max_diff_tokens=5)
    with pytest.raises(VcsDiffTooLargeError):
        client.fetch_revision(REF)


def test_all_errors_share_base_and_have_retryable():
    for error in (
        VcsAuthError,
        VcsNotFoundError,
        VcsRateLimitedError,
        VcsRevisionChangedError,
        VcsDiffTooLargeError,
        VcsDiffIncompleteError,
        VcsUpstreamError,
    ):
        assert issubclass(error, VcsError)
        assert isinstance(error("x").retryable, bool)


def test_constructor_requires_token_and_absolute_url():
    with pytest.raises(ValueError):
        GitHubClient(BASE, "")
    with pytest.raises(ValueError):
        GitHubClient("api.github.com", "t")
