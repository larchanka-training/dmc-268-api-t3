import enum
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.context import DiffContext
from app.ingestion.events import AcceptResult, VcsEvent
from app.vcs import RevisionSnapshot

Clock = Callable[[], datetime]


class IngestionDataError(Exception):
    """A deterministic value cannot be persisted.

    Carries only a value-free ``cause`` (the SQLSTATE such as ``22001``, or the
    driver exception class name) - never the driver message or bound parameters.
    """

    def __init__(self, cause: str) -> None:
        super().__init__("Value cannot be stored")
        self.cause = cause


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class DeliveryState:
    event: VcsEvent
    status: DeliveryStatus
    attempts: int
    locked_until: datetime | None
    next_attempt_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class Claim:
    event: VcsEvent
    lease: datetime


@dataclass(frozen=True)
class PreparedContext:
    snapshot: RevisionSnapshot
    context: DiffContext
    token_count: int
    provider_metadata: dict[str, str | int]


class DeliveryRepository(Protocol):
    """Persistence port for the delivery inbox.

    Transactional contract:

    - ``accept`` and ``mark_dispatched`` commit independently.
    - ``claimable`` and ``held`` each open one short transaction, lock the
      delivery row (``claimable`` skips rows locked by others) and yield a
      detached ``DeliveryState`` (``None`` if there is no such row). Changes made
      to the yielded state are applied only on normal exit of the ``with`` block;
      an exception rolls everything back. No transaction spans the VCS fetch.
    - ``save_success`` is one atomic transaction guarded by the lease in
      ``claim``. It returns ``None`` when the lease was lost, and raises
      ``IngestionDataError`` for a deterministic data error; any other database
      error propagates.
    - ``candidates`` is a read-only query.
    """

    def accept(self, event: VcsEvent) -> AcceptResult: ...
    def claimable(
        self, delivery_id: uuid.UUID, now: datetime
    ) -> AbstractContextManager[DeliveryState | None]: ...
    def held(
        self, delivery_id: uuid.UUID, claim: Claim
    ) -> AbstractContextManager[DeliveryState | None]: ...
    def save_success(
        self, delivery_id: uuid.UUID, claim: Claim, payload: PreparedContext
    ) -> uuid.UUID | None: ...
    def mark_dispatched(self, delivery_id: uuid.UUID, now: datetime) -> None: ...
    def candidates(self, now: datetime, stale_after: timedelta, limit: int) -> list[uuid.UUID]: ...


class DeliveryQueue(Protocol):
    def enqueue(self, delivery_id: uuid.UUID) -> bool:
        """Return True for a new job, False for an already queued job."""
