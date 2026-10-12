import asyncio
import json
from collections.abc import Callable, Iterator, MutableMapping
from typing import Any

import pytest
from redis import Redis
from sqlalchemy import exc as sa_exc
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import webhooks
from app.main import app
from app.models import VcsDelivery, VcsDeliveryStatus
from app.queue import get_queue
from tests.api.conftest import (
    GITHUB_SECRET,
    Api,
    failing_factory,
    fixture_bytes,
    github_headers,
    gitlab_signed_headers,
    gitlab_token_headers,
    unavailable_factory,
)

Factory = Callable[[], Session]
GH_OPENED = fixture_bytes("github_pull_request_opened.json")
GL_OPEN = fixture_bytes("gitlab_merge_request_open.json")


def _github(api: Api, body: bytes = GH_OPENED, **kwargs: Any) -> Any:
    return api.client.post("/webhooks/github", content=body, headers=github_headers(body, **kwargs))


def _gitlab(api: Api, body: bytes = GL_OPEN, **kwargs: Any) -> Any:
    return api.client.post(
        "/webhooks/gitlab", content=body, headers=gitlab_signed_headers(body, **kwargs)
    )


def _count(factory: Factory) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(VcsDelivery)) or 0


# --- authentication (the fixture's DB factory fails the test if it is called) ---


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Hub-Signature-256": "sha256=" + "0" * 64},
        {"X-Hub-Signature-256": "sha1=" + "0" * 40},
    ],
)
def test_github_missing_or_wrong_signature_is_401(api: Api, headers: dict[str, str]) -> None:
    response = api.client.post(
        "/webhooks/github",
        content=GH_OPENED,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d", **headers},
    )

    assert response.status_code == 401
    assert api.queue.job_ids == []


def test_github_tampered_body_is_401(api: Api) -> None:
    headers = github_headers(GH_OPENED)

    response = api.client.post("/webhooks/github", content=GH_OPENED + b" ", headers=headers)

    assert response.status_code == 401


def test_unauthenticated_unrelated_event_is_401_not_acknowledged(api: Api) -> None:
    body = fixture_bytes("github_ping.json")
    headers = github_headers(body, event="ping", secret="wrong")

    assert api.client.post("/webhooks/github", content=body, headers=headers).status_code == 401


def test_malformed_body_under_bad_signature_is_401_not_400(api: Api) -> None:
    headers = github_headers(b"{not json", secret="wrong")

    response = api.client.post("/webhooks/github", content=b"{not json", headers=headers)

    assert response.status_code == 401


def test_unconfigured_github_endpoint_fails_closed_with_401(api: Api) -> None:
    api.use_settings(github_webhook_secret="")

    assert _github(api).status_code == 401


def test_gitlab_signed_mode_rejects_bad_stale_and_token_only_requests(api: Api) -> None:
    assert _gitlab(api, key=b"another-key").status_code == 401
    assert _gitlab(api, timestamp=1).status_code == 401
    token_only = api.client.post(
        "/webhooks/gitlab", content=GL_OPEN, headers=gitlab_token_headers()
    )
    assert token_only.status_code == 401


def test_gitlab_token_mode_rejects_wrong_token(api: Api) -> None:
    api.use_settings(gitlab_webhook_auth="token")

    response = api.client.post(
        "/webhooks/gitlab", content=GL_OPEN, headers=gitlab_token_headers(token="nope")
    )

    assert response.status_code == 401


def test_gitlab_unknown_auth_mode_fails_closed(api: Api) -> None:
    api.use_settings(gitlab_webhook_auth="bogus")

    assert _gitlab(api).status_code == 401


def test_auth_failure_response_does_not_reveal_a_reason(api: Api) -> None:
    response = api.client.post("/webhooks/github", content=GH_OPENED)

    assert response.status_code == 401
    assert GITHUB_SECRET not in response.text
    assert "signature" not in response.text.lower()


# --- ignored events, invalid payloads, oversize (no database) -----------------


@pytest.mark.parametrize(
    ("name", "event"),
    [
        ("github_ping.json", "ping"),
        ("github_pull_request_closed.json", "pull_request"),
    ],
)
def test_github_unrelated_authenticated_events_are_ignored(api: Api, name: str, event: str) -> None:
    response = _github(api, fixture_bytes(name), event=event)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert api.queue.job_ids == []


@pytest.mark.parametrize(
    "name", ["gitlab_merge_request_close.json", "gitlab_merge_request_update_without_oldrev.json"]
)
def test_gitlab_unrelated_authenticated_events_are_ignored(api: Api, name: str) -> None:
    response = _gitlab(api, fixture_bytes(name))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_gitlab_other_hook_type_is_ignored(api: Api) -> None:
    assert _gitlab(api, event="Push Hook").json() == {"status": "ignored"}


@pytest.mark.parametrize("body", [b"{not json", b"[1]", b'{"action": "opened"}'])
def test_github_invalid_supported_payload_is_400(api: Api, body: bytes) -> None:
    response = _github(api, body)

    assert response.status_code == 400
    assert api.queue.job_ids == []


@pytest.mark.parametrize("bad", ["a\\u0000b", "a\\ud800b"], ids=["nul", "lone-surrogate"])
def test_string_field_the_database_cannot_store_is_400(api: Api, bad: str) -> None:
    body = GH_OPENED.replace(b"Add feature", bad.encode())

    response = _github(api, body)

    assert response.status_code == 400
    assert api.queue.job_ids == []


def test_gitlab_delivery_without_an_id_is_400(api: Api) -> None:
    api.use_settings(gitlab_webhook_auth="token")

    response = api.client.post(
        "/webhooks/gitlab", content=GL_OPEN, headers=gitlab_token_headers(delivery=None)
    )

    assert response.status_code == 400


def test_oversized_declared_body_is_413_before_authentication(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "MAX_BODY_BYTES", 100)

    response = api.client.post("/webhooks/github", content=b"x" * 101)  # unsigned

    assert response.status_code == 413


def test_oversized_streamed_body_is_413(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "MAX_BODY_BYTES", 100)

    def chunks() -> Iterator[bytes]:
        for _ in range(11):
            yield b"x" * 10

    response = api.client.post("/webhooks/github", content=chunks())

    assert response.status_code == 413


def test_body_at_the_limit_is_still_authenticated(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = fixture_bytes("github_ping.json")
    monkeypatch.setattr(webhooks, "MAX_BODY_BYTES", len(body))

    assert _github(api, body, event="ping").status_code == 200


def test_database_outage_is_503_without_a_2xx(api: Api) -> None:
    api.use_factory(unavailable_factory)

    response = _github(api)

    assert response.status_code == 503
    assert api.queue.job_ids == []


@pytest.mark.parametrize(
    "error",
    [
        sa_exc.OperationalError("SELECT 1", {}, Exception("down")),
        sa_exc.InterfaceError("SELECT 1", {}, Exception("closed")),
        sa_exc.TimeoutError("QueuePool limit reached"),
    ],
    ids=["operational", "interface", "pool-timeout"],
)
def test_transient_database_failures_are_503(api: Api, error: Exception) -> None:
    api.use_factory(failing_factory(error))

    assert _github(api).status_code == 503
    assert api.queue.job_ids == []


@pytest.mark.parametrize(
    "error",
    [
        sa_exc.DataError("INSERT", {}, Exception("bad data")),
        sa_exc.IntegrityError("INSERT", {}, Exception("constraint")),
    ],
    ids=["data", "integrity"],
)
def test_non_transient_database_errors_are_not_503(api: Api, error: Exception) -> None:
    api.use_factory(failing_factory(error))

    with pytest.raises(type(error)):
        _github(api)


def test_non_ascii_digit_content_length_is_not_a_server_error(api: Api) -> None:
    # "\xb2" (superscript two) satisfies str.isdigit() but not int(). The test client
    # rewrites Content-Length, so drive the ASGI app with a hand-built request.
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/webhooks/github",
        "raw_path": b"/webhooks/github",
        "query_string": b"",
        "headers": [(b"content-length", b"\xb2")],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    asyncio.run(app(scope, receive, send))

    assert sent[0]["status"] == 401


# --- durable acceptance (PostgreSQL) -------------------------------------------


@pytest.fixture()
def pg_api(api: Api, session_factory: Factory, redis_server: Redis) -> Api:
    api.use_factory(session_factory)
    api.use_queue(get_queue(redis_server))
    return api


def test_github_accepted_only_after_commit_and_one_job_enqueued(
    pg_api: Api, session_factory: Factory
) -> None:
    response = _github(pg_api)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    with session_factory() as session:
        row = session.scalars(select(VcsDelivery)).one()
    assert (row.provider, row.delivery_id, row.status) == (
        "github",
        "gh-delivery-1",
        VcsDeliveryStatus.PENDING,
    )
    assert (row.repository_external_id, row.pr_number, row.head_sha) == ("octo/repo", 42, "a" * 40)
    assert pg_api.queue.job_ids == [f"ingest-{row.id}"]


def test_gitlab_accepted_and_normalized_identity_is_persisted(
    pg_api: Api, session_factory: Factory
) -> None:
    response = _gitlab(pg_api)

    assert response.json() == {"status": "accepted"}
    with session_factory() as session:
        row = session.scalars(select(VcsDelivery)).one()
    assert (row.provider, row.repository_external_id, row.pr_number) == ("gitlab", "123", 7)
    assert (row.delivery_id, row.base_sha, row.author) == ("gl-delivery-1", None, "alice")
    assert pg_api.queue.job_ids == [f"ingest-{row.id}"]


def test_gitlab_token_mode_is_accepted(pg_api: Api, session_factory: Factory) -> None:
    pg_api.use_settings(gitlab_webhook_auth="token")

    response = pg_api.client.post(
        "/webhooks/gitlab", content=GL_OPEN, headers=gitlab_token_headers()
    )

    assert response.json() == {"status": "accepted"}
    assert _count(session_factory) == 1


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_repeated_delivery_is_duplicate_with_one_row_and_one_job(
    pg_api: Api, session_factory: Factory, provider: str
) -> None:
    send = _github if provider == "github" else _gitlab

    first = send(pg_api)
    second = send(pg_api)

    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert _count(session_factory) == 1
    assert len(pg_api.queue.job_ids) == 1


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_new_delivery_id_for_same_revision_is_duplicate(
    pg_api: Api, session_factory: Factory, provider: str
) -> None:
    send = _github if provider == "github" else _gitlab

    send(pg_api, delivery="first")
    again = send(pg_api, delivery="second")

    assert again.status_code == 200
    assert again.json() == {"status": "duplicate"}
    assert _count(session_factory) == 1
    assert len(pg_api.queue.job_ids) == 1


def test_new_head_revision_is_a_new_delivery(pg_api: Api, session_factory: Factory) -> None:
    _github(pg_api)
    body = fixture_bytes("github_pull_request_synchronize.json").replace(b"a" * 40, b"c" * 40)

    response = _github(pg_api, body, delivery="gh-delivery-2")

    assert response.json() == {"status": "accepted"}
    assert _count(session_factory) == 2
    assert len(pg_api.queue.job_ids) == 2


def test_queue_outage_still_accepts_and_leaves_row_for_recovery(
    pg_api: Api, session_factory: Factory
) -> None:
    unreachable = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2
    )
    pg_api.use_queue(get_queue(unreachable))

    response = _github(pg_api)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    with session_factory() as session:
        row = session.scalars(select(VcsDelivery)).one()
    assert row.status == VcsDeliveryStatus.PENDING
    assert row.last_dispatched_at is None


def test_ignored_invalid_and_unauthenticated_requests_leave_no_rows(
    pg_api: Api, session_factory: Factory
) -> None:
    _github(pg_api, fixture_bytes("github_ping.json"), event="ping")
    _github(pg_api, b"{}")
    pg_api.client.post("/webhooks/github", content=GH_OPENED)

    assert _count(session_factory) == 0
    assert pg_api.queue.job_ids == []


@pytest.mark.parametrize("bad", ["a\\u0000b", "a\\ud800b"], ids=["nul", "lone-surrogate"])
def test_unstorable_string_is_400_and_leaves_no_row(
    pg_api: Api, session_factory: Factory, bad: str
) -> None:
    response = _github(pg_api, GH_OPENED.replace(b"Add feature", bad.encode()))

    assert response.status_code == 400
    assert _count(session_factory) == 0
    assert pg_api.queue.job_ids == []


def test_response_body_is_only_a_status(pg_api: Api) -> None:
    assert json.loads(_github(pg_api).content) == {"status": "accepted"}
