import uuid

from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.ingestion.dispatch import job_id_for
from app.ingestion.service import DEFAULT_LEASE
from app.queue import get_queue

INGESTION_JOB = "app.worker.ingestion.run_ingestion"


def _existing_job(queue: Queue, job_id: str) -> Job | None:
    try:
        return Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError:
        return None


def _enqueue(queue: Queue, delivery_id: uuid.UUID) -> bool:
    """Return True if a new job was enqueued, False if one is already waiting."""
    job_id = job_id_for(delivery_id)
    existing = _existing_job(queue, job_id)
    if existing is not None:
        # Still waiting in the list: a worker will pick it up. A "queued" hash whose
        # id is no longer in the list (worker died right after popping) is stale.
        if (
            existing.get_status() == "queued"
            and queue.connection.lpos(queue.key, job_id) is not None
        ):
            return False
        # RQ keeps finished/failed jobs (and a crashed worker's "started" one) under
        # their id; a fresh job with the same deterministic id needs the stale one gone.
        existing.delete()
    queue.enqueue(
        INGESTION_JOB,
        str(delivery_id),
        job_id=job_id,
        # Stop the job before its DB lease would expire so a live worker never
        # overlaps with a recovery re-dispatch of the same row.
        job_timeout=int(DEFAULT_LEASE.total_seconds()),
    )
    return True


class RqDeliveryQueue:
    """``DeliveryQueue`` over RQ; the queue is resolved on first use, once."""

    def __init__(self, queue: Queue | None = None) -> None:
        self._queue = queue

    def enqueue(self, delivery_id: uuid.UUID) -> bool:
        if self._queue is None:
            self._queue = get_queue()
        return _enqueue(self._queue, delivery_id)
