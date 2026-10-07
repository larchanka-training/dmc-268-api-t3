from redis import Redis
from rq import Queue

from app.config import settings

QUEUE_NAME = "default"


def get_redis(url: str | None = None) -> Redis:
    # Redis.from_url does not connect until the first command, so importing
    # this module (or starting the API) never needs a reachable Redis.
    return Redis.from_url(url or settings.redis_url)


def get_queue(connection: Redis | None = None) -> Queue:
    return Queue(QUEUE_NAME, connection=connection or get_redis())
