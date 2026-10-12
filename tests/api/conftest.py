import base64
import hashlib
import hmac
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn

import pytest
from fakeredis import FakeRedis, FakeServer
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy.exc import OperationalError

from app.api.webhooks import get_session_factory, get_settings, get_webhook_queue
from app.config import Settings, load_settings
from app.main import app
from app.queue import get_queue

# Reuse the PostgreSQL fixtures of the ingestion tests.
from tests.db.conftest import alembic_config, pg_engine  # noqa: F401
from tests.ingestion.conftest import redis_server, session_factory  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[1] / "webhooks" / "fixtures"
GITHUB_SECRET = "gh-test-secret"
GITLAB_SIGNING_KEY = b"gitlab-test-signing-key-0123456789"
GITLAB_SIGNING_TOKEN = "whsec_" + base64.b64encode(GITLAB_SIGNING_KEY).decode()
GITLAB_SECRET_TOKEN = "gl-test-token"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def github_headers(
    body: bytes,
    *,
    delivery: str = "gh-delivery-1",
    event: str = "pull_request",
    secret: str = GITHUB_SECRET,
) -> dict[str, str]:
    """Sign the exact bytes that will be sent, per the GitHub protocol."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }


def gitlab_signed_headers(
    body: bytes,
    *,
    delivery: str = "gl-delivery-1",
    event: str = "Merge Request Hook",
    timestamp: int | None = None,
    key: bytes = GITLAB_SIGNING_KEY,
) -> dict[str, str]:
    """Standard Webhooks signature as sent by GitLab signed webhooks."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signed = f"{delivery}.{ts}.".encode() + body
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": delivery,
        "webhook-timestamp": ts,
        "webhook-signature": f"v1,{signature}",
        "X-Gitlab-Event": event,
        "Content-Type": "application/json",
    }


def gitlab_token_headers(
    *,
    delivery: str | None = "gl-delivery-1",
    event: str = "Merge Request Hook",
    token: str = GITLAB_SECRET_TOKEN,
) -> dict[str, str]:
    headers = {
        "X-Gitlab-Token": token,
        "X-Gitlab-Event": event,
        "Content-Type": "application/json",
    }
    if delivery is not None:
        headers["Idempotency-Key"] = delivery
    return headers


def forbidden_factory() -> NoReturn:
    """Session factory that fails the test if it is called at all."""
    pytest.fail("the database must not be touched for this request")


class _FailingSession:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> "_FailingSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        def fail(*_: object, **__: object) -> NoReturn:
            raise self._error

        return fail


def failing_factory(error: Exception) -> Callable[[], _FailingSession]:
    return lambda: _FailingSession(error)


def unavailable_factory() -> _FailingSession:
    return _FailingSession(OperationalError("SELECT 1", {}, Exception("connection refused")))


@dataclass
class Api:
    client: TestClient
    queue: Queue
    use_factory: Callable[[Callable[[], Any]], None]
    use_queue: Callable[[Queue], None]
    use_settings: Callable[..., Settings]


@pytest.fixture()
def api() -> Iterator[Api]:
    """TestClient with a fail-if-called DB factory, a fakeredis queue and test secrets."""
    queue = get_queue(FakeRedis(server=FakeServer()))
    current: dict[str, Any] = {
        "settings": replace(
            load_settings(),
            github_webhook_secret=GITHUB_SECRET,
            gitlab_webhook_auth="signed",
            gitlab_webhook_signing_token=GITLAB_SIGNING_TOKEN,
            gitlab_webhook_secret_token=GITLAB_SECRET_TOKEN,
        ),
        "factory": forbidden_factory,
        "queue": queue,
    }
    app.dependency_overrides[get_settings] = lambda: current["settings"]
    app.dependency_overrides[get_session_factory] = lambda: current["factory"]
    app.dependency_overrides[get_webhook_queue] = lambda: current["queue"]

    def use_factory(factory: Callable[[], Any]) -> None:
        current["factory"] = factory

    def use_queue(new_queue: Queue) -> None:
        current["queue"] = new_queue
        harness.queue = new_queue

    def use_settings(**overrides: Any) -> Settings:
        current["settings"] = replace(current["settings"], **overrides)
        return current["settings"]  # type: ignore[no-any-return]

    harness = Api(TestClient(app), queue, use_factory, use_queue, use_settings)
    try:
        yield harness
    finally:
        app.dependency_overrides.clear()
