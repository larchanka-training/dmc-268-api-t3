from fakeredis import FakeRedis
from rq import Queue, SimpleWorker

from app.queue import QUEUE_NAME, get_queue
from app.worker.smoke import run_smoke
from app.worker.tasks import ping


def test_ping_returns_pong():
    assert ping("hello") == "pong: hello"


def test_get_queue_uses_default_queue_name():
    assert get_queue(FakeRedis()).name == QUEUE_NAME


def test_worker_processes_enqueued_job():
    connection = FakeRedis()
    queue = get_queue(connection)
    job = queue.enqueue(ping, "hello")

    SimpleWorker([queue], connection=connection).work(burst=True)

    assert job.get_status(refresh=True) == "finished"
    assert job.return_value() == "pong: hello"


def test_run_smoke_succeeds_when_job_runs():
    # is_async=False executes the job inline at enqueue time, standing in for a worker.
    queue = Queue(QUEUE_NAME, connection=FakeRedis(), is_async=False)

    assert run_smoke(queue, timeout_seconds=5) is True


def test_run_smoke_fails_when_no_worker_picks_up_the_job():
    queue = get_queue(FakeRedis())

    assert run_smoke(queue, timeout_seconds=0.6) is False
