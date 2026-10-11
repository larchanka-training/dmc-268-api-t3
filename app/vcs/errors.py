class VcsError(Exception):
    """Base class for retrieval failures.

    ``retryable`` tells the caller (the phase 3 worker) whether trying again
    can succeed. Messages never contain credentials.
    """

    retryable: bool = False

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class VcsAuthError(VcsError):
    retryable = False


class VcsNotFoundError(VcsError):
    retryable = False


class VcsRateLimitedError(VcsError):
    retryable = True


class VcsRevisionChangedError(VcsError):
    """Head/base moved during retrieval or differs from the webhook's expected head."""

    retryable = False


class VcsDiffTooLargeError(VcsError):
    retryable = False


class VcsDiffIncompleteError(VcsError):
    retryable = False


class VcsUpstreamError(VcsError):
    def __init__(
        self, message: str, *, retryable: bool = True, retry_after: float | None = None
    ) -> None:
        super().__init__(message, retry_after=retry_after)
        self.retryable = retryable
