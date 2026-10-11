from collections.abc import Callable

import httpx
import pytest

from app.llm.tokens import approximate_token_count
from app.vcs import (
    GitLabClient,
    PullRequestRef,
    VcsAuthError,
    VcsDiffIncompleteError,
    VcsDiffTooLargeError,
    VcsNotFoundError,
    VcsRateLimitedError,
    VcsRevisionChangedError,
    VcsUpstreamError,
)

BASE = "https://gitlab.example.test/api/v4"
TOKEN = "glpat-supersecret"
HEAD = "a" * 40
BASE_SHA = "b" * 40
START = "c" * 40
DIFF = (
    "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+diff --git fake\n"
)
REF = PullRequestRef(provider="gitlab", repository="42", number=7, expected_head_sha=HEAD)
MR_URL = f"{BASE}/projects/42/merge_requests/7"


def meta(head: str = HEAD, base: str = BASE_SHA, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "title": "Add thing",
        "state": "opened",
        "sha": head,
        "source_branch": "feature",
        "target_branch": "main",
        "changes_count": "2",
        "diff_refs": {"base_sha": base, "head_sha": head, "start_sha": START},
    }
    body.update(extra)
    return body


Handler = Callable[[httpx.Request], httpx.Response]


def is_diff(request: httpx.Request) -> bool:
    return request.url.path.endswith("/raw_diffs")


def make(handler: Handler, **kwargs: object) -> tuple[GitLabClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(recording))
    return GitLabClient(BASE, TOKEN, client=client, **kwargs), seen  # type: ignore[arg-type]


def ok_handler(diff: bytes | str = DIFF, **meta_kwargs: object) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if is_diff(request):
            return httpx.Response(200, content=diff if isinstance(diff, bytes) else diff.encode())
        return httpx.Response(200, json=meta(**meta_kwargs))  # type: ignore[arg-type]

    return handler


def test_fetches_metadata_diff_metadata_with_private_token():
    client, seen = make(ok_handler())

    snap = client.fetch_revision(REF)

    assert snap.provider == "gitlab"
    assert snap.repository == "42"
    assert snap.raw_diff == DIFF
    assert (snap.head_sha, snap.base_sha) == (HEAD, BASE_SHA)
    assert (snap.title, snap.state, snap.head_ref, snap.base_ref) == (
        "Add thing",
        "opened",
        "feature",
        "main",
    )
    assert [is_diff(r) for r in seen] == [False, True, False]
    assert [str(r.url) for r in seen] == [MR_URL, f"{MR_URL}/raw_diffs", MR_URL]
    for request in seen:
        assert request.headers["private-token"] == TOKEN
        assert "authorization" not in request.headers


def test_raw_diffs_is_a_single_unpaginated_request():
    client, seen = make(ok_handler())
    client.fetch_revision(REF)
    diff_requests = [r for r in seen if is_diff(r)]
    assert len(diff_requests) == 1
    assert diff_requests[0].url.query == b""


@pytest.mark.parametrize("bad", ["", "abc", "a/b", "../x", "4 2", "-1", "١٢"])
def test_repository_must_be_numeric_project_id(bad):
    client, seen = make(ok_handler())
    with pytest.raises(ValueError):
        client.fetch_revision(PullRequestRef("gitlab", bad, 1, HEAD))
    with pytest.raises(ValueError):
        client.fetch_revision(PullRequestRef("gitlab", "42", 0, HEAD))
    assert seen == []


def test_redirect_is_not_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.test/x"})

    client, seen = make(handler)
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable
    assert len(seen) == 1


def test_expected_head_mismatch_raises_before_diff():
    client, seen = make(ok_handler(head="d" * 40))
    with pytest.raises(VcsRevisionChangedError):
        client.fetch_revision(REF)
    assert len(seen) == 1


def test_diff_refs_not_matching_sha_is_retryable_not_ready():
    body = meta(diff_refs={"base_sha": BASE_SHA, "head_sha": "e" * 40, "start_sha": START})
    client, seen = make(lambda request: httpx.Response(200, json=body))
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert info.value.retryable
    assert len(seen) == 1


@pytest.mark.parametrize("refs", [{}, None, {"base_sha": BASE_SHA, "head_sha": HEAD}])
def test_empty_diff_refs_is_retryable_upstream(refs):
    client, _ = make(lambda request: httpx.Response(200, json=meta(diff_refs=refs)))
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert info.value.retryable


@pytest.mark.parametrize(
    "changed",
    [
        {
            "sha": "d" * 40,
            "diff_refs": {"base_sha": BASE_SHA, "head_sha": "d" * 40, "start_sha": START},
        },
        {"diff_refs": {"base_sha": "d" * 40, "head_sha": HEAD, "start_sha": START}},
        {"diff_refs": {"base_sha": BASE_SHA, "head_sha": HEAD, "start_sha": "d" * 40}},
    ],
)
def test_revision_changing_during_fetch_raises(changed):
    calls = {"meta": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_diff(request):
            return httpx.Response(200, text=DIFF)
        calls["meta"] += 1
        return httpx.Response(200, json=meta() if calls["meta"] == 1 else meta(**changed))

    client, _ = make(handler)
    with pytest.raises(VcsRevisionChangedError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable


@pytest.mark.parametrize(
    ("status", "headers", "error", "retryable"),
    [
        (401, {}, VcsAuthError, False),
        (403, {}, VcsAuthError, False),
        (404, {}, VcsNotFoundError, False),
        (429, {"retry-after": "12"}, VcsRateLimitedError, True),
        (429, {}, VcsRateLimitedError, True),
        (500, {}, VcsUpstreamError, True),
        (503, {}, VcsUpstreamError, True),
        (418, {}, VcsUpstreamError, False),
    ],
)
def test_status_mapping_on_metadata(status, headers, error, retryable):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json={"message": "x"})

    client, seen = make(handler, clock=lambda: 1000.0)
    with pytest.raises(error) as info:
        client.fetch_revision(REF)
    assert type(info.value) is error
    assert info.value.retryable is retryable
    assert len(seen) == 1  # no internal retries
    assert TOKEN not in str(info.value)


def test_rate_limit_retry_after_header_and_reset_fallback():
    def make_handler(headers: dict[str, str]) -> Handler:
        return lambda request: httpx.Response(429, headers=headers)

    client, _ = make(
        make_handler({"retry-after": "5", "ratelimit-reset": "1030"}), clock=lambda: 1000.0
    )
    with pytest.raises(VcsRateLimitedError) as info:
        client.fetch_revision(REF)
    assert info.value.retry_after == 5.0

    client, _ = make(make_handler({"ratelimit-reset": "1030"}), clock=lambda: 1000.0)
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

    for status, error in ((404, VcsNotFoundError), (429, VcsRateLimitedError)):
        client, _ = make(handler(status))
        with pytest.raises(error):
            client.fetch_revision(REF)


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
        httpx.Response(200, json={"title": "x"}),
        httpx.Response(200, json={"sha": 1}),
    ],
)
def test_malformed_metadata_is_upstream_error(response):
    client, _ = make(lambda request: response)
    with pytest.raises(VcsUpstreamError):
        client.fetch_revision(REF)


def test_empty_diff_is_valid_when_zero_changes():
    client, _ = make(ok_handler(diff="", changes_count="0"))
    assert client.fetch_revision(REF).raw_diff == ""


def test_non_utf8_diff_is_error():
    client, _ = make(ok_handler(diff=b"\xff\xfe\x00bad", changes_count="0"))
    with pytest.raises(VcsUpstreamError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable


@pytest.mark.parametrize("count", ["1000+", "100+", "+", "", None, "abc", "-1", "2.0", [], True])
def test_unprovable_changes_count_is_incomplete(count):
    client, seen = make(ok_handler(changes_count=count))
    with pytest.raises(VcsDiffIncompleteError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable
    assert len(seen) == 1  # fails before reading the diff


def test_changes_count_missing_is_incomplete():
    body = meta()
    del body["changes_count"]
    client, _ = make(lambda request: httpx.Response(200, json=body))
    with pytest.raises(VcsDiffIncompleteError):
        client.fetch_revision(REF)


@pytest.mark.parametrize("flag", ["overflow", "too_large", "diff_overflow"])
def test_overflow_flags_fail_closed(flag):
    client, _ = make(ok_handler(**{flag: True}))  # type: ignore[arg-type]
    with pytest.raises(VcsDiffIncompleteError):
        client.fetch_revision(REF)


@pytest.mark.parametrize("count", ["3", "1"])
def test_file_count_mismatch_is_incomplete(count):
    client, _ = make(ok_handler(changes_count=count))
    with pytest.raises(VcsDiffIncompleteError):
        client.fetch_revision(REF)


def test_integer_changes_count_is_accepted():
    client, _ = make(ok_handler(changes_count=2))
    assert client.fetch_revision(REF).raw_diff == DIFF


def _body_of_tokens(tokens: int, *, multibyte: bool = False) -> str:
    char = "é" if multibyte else "a"
    text = char * (tokens * 4)
    assert approximate_token_count(text) == tokens
    return text


def test_diff_at_limit_is_accepted_and_just_over_is_rejected():
    at_limit = _body_of_tokens(32000)
    client, _ = make(ok_handler(diff=at_limit, changes_count="0"))
    assert client.fetch_revision(REF).raw_diff == at_limit

    client, _ = make(ok_handler(diff=at_limit + "aaaa", changes_count="0"))
    with pytest.raises(VcsDiffTooLargeError) as info:
        client.fetch_revision(REF)
    assert not info.value.retryable


def test_multibyte_diff_within_token_limit_is_accepted():
    text = _body_of_tokens(32000, multibyte=True)
    client, _ = make(ok_handler(diff=text, changes_count="0"))
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

    client, _ = make(handler, max_diff_tokens=1000)
    with pytest.raises(VcsDiffTooLargeError):
        client.fetch_revision(REF)
    assert produced["bytes"] < 100_000


def test_constructor_requires_token_and_absolute_url():
    with pytest.raises(ValueError):
        GitLabClient(BASE, "")
    with pytest.raises(ValueError):
        GitLabClient("gitlab.com/api/v4", "t")
