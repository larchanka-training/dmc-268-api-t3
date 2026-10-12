"""Header helpers shared by webhook authentication and normalization (framework-free)."""

from collections.abc import Mapping


def get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lowered:
            return candidate
    return None
