from dataclasses import dataclass

import httpx

from app.llm.tokens import approximate_token_count
from app.vcs.errors import (
    VcsAuthError,
    VcsDiffTooLargeError,
    VcsError,
    VcsNotFoundError,
    VcsRateLimitedError,
    VcsUpstreamError,
)

# Worst case: 4 characters per approximate token, 4 UTF-8 bytes per character.
BYTES_PER_TOKEN_CEILING = 4 * 4
MAX_METADATA_BYTES = 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024


def byte_ceiling(max_tokens: int) -> int:
    return max_tokens * BYTES_PER_TOKEN_CEILING


@dataclass(frozen=True)
class BoundedResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes
    exceeded: bool  # body was cut off at the byte limit (never use it as content)


def get_bounded(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int,
) -> BoundedResponse:
    """GET without following redirects, reading at most ``max_bytes`` of a 2xx body.

    Non-2xx bodies are read up to a small cap for error classification only.
    """
    try:
        with client.stream(
            "GET", url, headers=headers, timeout=timeout, follow_redirects=False
        ) as response:
            limit = max_bytes if response.is_success else MAX_ERROR_BODY_BYTES
            chunks: list[bytes] = []
            total = 0
            exceeded = False
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    exceeded = True
                    break
                chunks.append(chunk)
            return BoundedResponse(
                status_code=response.status_code,
                headers=response.headers,
                body=b"".join(chunks),
                exceeded=exceeded,
            )
    except httpx.TimeoutException as exc:
        raise VcsUpstreamError("Provider request timed out") from exc
    except httpx.HTTPError as exc:
        raise VcsUpstreamError(f"Provider request failed ({type(exc).__name__})") from exc


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def classify_error_response(
    response: BoundedResponse, *, rate_limited: bool, retry_after: float | None = None
) -> VcsError:
    """Shared status -> typed error mapping (provider decides what counts as throttling)."""
    status = response.status_code
    if rate_limited or status == 429:
        return VcsRateLimitedError(
            f"Provider rate limited the request ({status})", retry_after=retry_after
        )
    if status in (401, 403):
        return VcsAuthError(f"Provider rejected credentials ({status})")
    if status == 404:
        return VcsNotFoundError("Pull request or repository not found")
    if status >= 500:
        return VcsUpstreamError(f"Provider unavailable ({status})")
    if 300 <= status < 400:
        return VcsUpstreamError(f"Unexpected redirect ({status}) not followed", retryable=False)
    return VcsUpstreamError(f"Unexpected provider response ({status})", retryable=False)


def decode_diff(response: BoundedResponse, max_tokens: int) -> str:
    """Validate a successful diff body and return it whole, or raise. Never truncates."""
    if response.exceeded:
        raise VcsDiffTooLargeError(f"Diff exceeds the {max_tokens} token limit")
    try:
        text = response.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VcsUpstreamError("Diff is not valid UTF-8", retryable=False) from exc
    if approximate_token_count(text) > max_tokens:
        raise VcsDiffTooLargeError(f"Diff exceeds the {max_tokens} token limit")
    return text
