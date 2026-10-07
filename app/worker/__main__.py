from rq import Worker

from app.queue import get_queue, get_redis


def main() -> None:
    connection = get_redis()
    Worker([get_queue(connection)], connection=connection).work()


if __name__ == "__main__":
    main()
