import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion import IngestResult, ingest_delivery
from app.ingestion.service import utc_now
from app.vcs import (
    GitHubClient,
    GitLabClient,
    PullRequestRef,
    RevisionSnapshot,
    VcsClient,
    VcsInvalidRequestError,
)


class SettingsVcsClient:
    """``VcsClient`` that builds the real provider client from settings per call.

    Construction is lazy: a missing token, unknown provider or malformed
    repository identity surfaces after the claim as a terminal
    ``VcsInvalidRequestError``, so the delivery fails with a recorded
    ``last_error`` instead of being retried.
    """

    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
        with httpx.Client() as http:
            client: VcsClient
            if ref.provider == "github":
                client = GitHubClient(
                    settings.github_api_base_url,
                    settings.github_token,
                    timeout=settings.vcs_timeout_seconds,
                    max_diff_tokens=settings.vcs_max_diff_tokens,
                    client=http,
                )
            elif ref.provider == "gitlab":
                client = GitLabClient(
                    settings.gitlab_api_base_url,
                    settings.gitlab_token,
                    timeout=settings.vcs_timeout_seconds,
                    max_diff_tokens=settings.vcs_max_diff_tokens,
                    client=http,
                )
            else:
                raise VcsInvalidRequestError(f"Unsupported VCS provider: {ref.provider}")
            return client.fetch_revision(ref)


@dataclass(frozen=True)
class Runtime:
    session_factory: Callable[[], Session]
    client: VcsClient
    clock: Callable[[], datetime]


def _runtime() -> Runtime:
    # Imported here so importing this module never builds a DB engine.
    from app.database import SessionLocal  # pylint: disable=import-outside-toplevel

    return Runtime(SessionLocal, SettingsVcsClient(), utc_now)


def run_ingestion(delivery_id: str) -> str:
    """RQ job: ingest one inbox delivery; harmless if it is not claimable."""
    runtime = _runtime()
    result: IngestResult = ingest_delivery(
        runtime.session_factory,
        runtime.client,
        uuid.UUID(delivery_id),
        clock=runtime.clock,
    )
    return str(result.outcome)
