"""Thin HTTP layer for VCS webhooks: read bytes, authenticate, normalize, accept."""

import functools
import logging
import re
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from redis import Redis
from rq import Queue
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.ingestion import AcceptOutcome, VcsEvent
from app.ingestion.dispatch import accept_and_dispatch
from app.queue import QUEUE_NAME
from app.webhooks.auth import verify_github, verify_gitlab
from app.webhooks.errors import WebhookAuthError, WebhookPayloadError
from app.webhooks.events import IgnoredEvent
from app.webhooks.github import normalize_github
from app.webhooks.gitlab import normalize_gitlab

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")

MAX_BODY_BYTES = 25 * 1024 * 1024
# A blackholed Redis must not push the response past GitHub's 10 s limit.
REDIS_TIMEOUT_SECONDS = 2.0

_ASCII_DIGITS = re.compile(r"[0-9]+")

SessionFactory = Callable[[], Session]


def get_settings() -> Settings:
    from app.config import settings  # pylint: disable=import-outside-toplevel

    return settings


def get_session_factory() -> SessionFactory:
    # Imported lazily so importing the app never builds a database engine.
    from app.database import SessionLocal  # pylint: disable=import-outside-toplevel

    return SessionLocal


@functools.lru_cache(maxsize=1)
def _webhook_queue(redis_url: str) -> Queue:
    connection = Redis.from_url(
        redis_url,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    return Queue(QUEUE_NAME, connection=connection)


def get_webhook_queue(settings: Annotated[Settings, Depends(get_settings)]) -> Queue:
    return _webhook_queue(settings.redis_url)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and _ASCII_DIGITS.fullmatch(declared)
        and int(declared) > MAX_BODY_BYTES
    ):
        raise HTTPException(status_code=413, detail="payload too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _authenticate_normalize(
    provider: str, settings: Settings, headers: dict[str, str], body: bytes, now: float
) -> VcsEvent | IgnoredEvent:
    if provider == "github":
        verify_github(settings.github_webhook_secret, body, headers)
        return normalize_github(headers, body)
    verify_gitlab(
        settings.gitlab_webhook_auth,
        signing_token=settings.gitlab_webhook_signing_token,
        secret_token=settings.gitlab_webhook_secret_token,
        body=body,
        headers=headers,
        now=now,
    )
    return normalize_gitlab(headers, body)


async def _handle(
    provider: str,
    request: Request,
    settings: Settings,
    factory: SessionFactory,
    queue: Queue,
) -> dict[str, str]:
    body = await _read_body(request)
    headers = dict(request.headers)
    try:
        event = await run_in_threadpool(
            _authenticate_normalize, provider, settings, headers, body, time.time()
        )
    except WebhookAuthError as exc:
        logger.warning("Webhook rejected: provider=%s outcome=unauthorized", provider)
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    except WebhookPayloadError as exc:
        logger.warning("Webhook rejected: provider=%s outcome=invalid reason=%s", provider, exc)
        raise HTTPException(status_code=400, detail="invalid payload") from exc
    if isinstance(event, IgnoredEvent):
        logger.info("Webhook ignored: provider=%s reason=%s", provider, event.reason)
        return {"status": "ignored"}
    try:
        result = await run_in_threadpool(accept_and_dispatch, factory, event, queue=queue)
    except (OperationalError, InterfaceError, PoolTimeoutError) as exc:
        logger.error("Webhook not accepted: database unavailable: provider=%s", provider)
        raise HTTPException(status_code=503, detail="service unavailable") from exc
    logger.info(
        "Webhook processed: provider=%s action=%s delivery=%s outcome=%s",
        provider,
        event.action,
        event.delivery_id,
        result.outcome,
    )
    return {"status": "accepted" if result.outcome == AcceptOutcome.CREATED else "duplicate"}


@router.post("/github")
async def github_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
    queue: Annotated[Queue, Depends(get_webhook_queue)],
) -> dict[str, str]:
    return await _handle("github", request, settings, factory, queue)


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
    queue: Annotated[Queue, Depends(get_webhook_queue)],
) -> dict[str, str]:
    return await _handle("gitlab", request, settings, factory, queue)
