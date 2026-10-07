import sys
import time

from rq import Queue

from app.queue import get_queue
from app.worker.tasks import ping


def run_smoke(queue: Queue | None = None, timeout_seconds: float = 30.0) -> bool:
    job = (queue or get_queue()).enqueue(ping, "smoke")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if job.get_status(refresh=True) == "finished":
            return bool(job.return_value() == "pong: smoke")
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    sys.exit(0 if run_smoke() else 1)
