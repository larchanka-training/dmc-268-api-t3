import logging
import signal
import threading
from collections.abc import Callable
from datetime import timedelta

from rq import Queue

from app.config import settings
from app.ingestion.dispatch import dispatch_pending
from app.ingestion.service import Clock, SessionFactory, utc_now

logger = logging.getLogger(__name__)


def run_loop(  # pylint: disable=too-many-arguments
    factory: SessionFactory,
    queue: Queue,
    *,
    stop: threading.Event,
    interval: float,
    stale_after: timedelta,
    clock: Clock = utc_now,
    wait: Callable[[float], bool] | None = None,
) -> None:
    """Run ``dispatch_pending`` every ``interval`` seconds until ``stop`` is set.

    ``wait`` (default ``stop.wait``) returns True when the loop should end; tests
    inject it so no real sleeping is needed. Tick errors are logged, never fatal.
    """
    wait = wait or stop.wait
    while not stop.is_set():
        try:
            count = dispatch_pending(factory, queue, clock=clock, stale_after=stale_after)
            if count:
                logger.info("Recovery dispatched %d deliveries", count)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Recovery tick failed")
        if wait(interval):
            return


def main() -> None:
    from app.database import SessionLocal  # pylint: disable=import-outside-toplevel
    from app.queue import get_queue  # pylint: disable=import-outside-toplevel

    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run_loop(
        SessionLocal,
        get_queue(),
        stop=stop,
        interval=settings.recovery_interval_seconds,
        stale_after=timedelta(seconds=settings.recovery_stale_after_seconds),
    )


if __name__ == "__main__":
    main()
