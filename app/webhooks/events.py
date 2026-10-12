from dataclasses import dataclass


@dataclass(frozen=True)
class IgnoredEvent:
    """Authenticated delivery that is valid but creates no ingestion work."""

    reason: str
