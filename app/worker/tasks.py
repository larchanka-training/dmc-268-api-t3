def ping(message: str) -> str:
    # Placeholder job proving the enqueue -> Redis -> worker path end to end.
    # Real review jobs arrive with issue #13.
    return f"pong: {message}"
