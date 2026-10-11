# The two adapters intentionally mirror each other behind the shared VcsClient port.
# pylint: disable=duplicate-code
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.vcs.errors import (
    VcsDiffIncompleteError,
    VcsInvalidRequestError,
    VcsRevisionChangedError,
    VcsUpstreamError,
)
from app.vcs.http import (
    MAX_METADATA_BYTES,
    BoundedResponse,
    byte_ceiling,
    classify_error_response,
    decode_diff,
    get_bounded,
    parse_retry_after,
)
from app.vcs.models import PullRequestRef, RevisionSnapshot

PROVIDER = "github"
API_VERSION = "2022-11-28"
JSON_ACCEPT = "application/vnd.github+json"
DIFF_ACCEPT = "application/vnd.github.diff"
DEFAULT_MAX_DIFF_TOKENS = 32000


@dataclass(frozen=True)
class _Metadata:
    title: str
    state: str
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    changed_files: int | None


class GitHubClient:
    """Retrieves PR metadata plus the complete raw diff for one consistent revision.

    No internal retries: errors carry ``retryable`` for the caller's policy.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        max_diff_tokens: int = DEFAULT_MAX_DIFF_TOKENS,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise VcsInvalidRequestError("GitHubClient requires an absolute http(s) base URL")
        if not token:
            raise VcsInvalidRequestError("GitHubClient requires a token")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._max_diff_tokens = max_diff_tokens
        self._client = client or httpx.Client()
        self._clock = clock

    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
        url = self._pull_url(ref)
        expected = ref.expected_head_sha.strip().lower()
        if not expected:
            raise VcsInvalidRequestError("expected_head_sha is required")

        first = self._fetch_metadata(url)
        if first.head_sha.lower() != expected:
            raise VcsRevisionChangedError("Pull request head differs from the expected revision")

        raw_diff = self._fetch_diff(url)

        second = self._fetch_metadata(url)
        if (second.head_sha, second.base_sha) != (first.head_sha, first.base_sha):
            raise VcsRevisionChangedError("Pull request revision changed during retrieval")

        # Each file section starts with "diff --git" at line start; content lines are
        # always prefixed (+, -, space), so this cannot match diff body text.
        if first.changed_files is not None:
            header_count = sum(1 for line in raw_diff.split("\n") if line.startswith("diff --git "))
            if header_count != first.changed_files:
                raise VcsDiffIncompleteError(
                    f"Diff has {header_count} files but metadata reports {first.changed_files}"
                )

        return RevisionSnapshot(
            provider=PROVIDER,
            repository=ref.repository,
            number=ref.number,
            title=first.title,
            state=first.state,
            head_sha=first.head_sha,
            base_sha=first.base_sha,
            head_ref=first.head_ref,
            base_ref=first.base_ref,
            raw_diff=raw_diff,
        )

    def _pull_url(self, ref: PullRequestRef) -> str:
        parts = ref.repository.split("/")
        if len(parts) != 2 or any(p in ("", ".", "..") for p in parts):
            raise VcsInvalidRequestError("repository must be 'owner/name'")
        if ref.number <= 0:
            raise VcsInvalidRequestError("pull request number must be positive")
        owner, repo = (quote(p, safe="") for p in parts)
        return f"{self._base_url}/repos/{owner}/{repo}/pulls/{ref.number}"

    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "dmc-268-vcs-client",
        }

    def _is_rate_limited(self, response: BoundedResponse) -> tuple[bool, float | None]:
        # Documented: primary and secondary limits answer 403 or 429; primary has
        # x-ratelimit-remaining: 0 (reset in epoch seconds), secondary has retry-after
        # and/or a message mentioning "rate limit".
        if response.status_code not in (403, 429):
            return False, None
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        limited = response.status_code == 429
        if response.headers.get("x-ratelimit-remaining") == "0":
            limited = True
            reset = response.headers.get("x-ratelimit-reset")
            if retry_after is None and reset is not None and reset.isdigit():
                retry_after = max(0.0, float(reset) - self._clock())
        if retry_after is not None:
            limited = True
        if b"rate limit" in response.body.lower():
            limited = True
        return limited, retry_after

    def _get(self, url: str, accept: str, max_bytes: int) -> BoundedResponse:
        response = get_bounded(
            self._client,
            url,
            headers=self._headers(accept),
            timeout=self._timeout,
            max_bytes=max_bytes,
        )
        if not 200 <= response.status_code < 300:
            limited, retry_after = self._is_rate_limited(response)
            if accept == DIFF_ACCEPT and response.status_code in (406, 422):
                # GitHub rejects diffs it cannot render in full with 406/422. The docs
                # do not document the exact signal, so any such answer fails closed.
                raise VcsDiffIncompleteError(
                    f"Provider could not produce a complete diff ({response.status_code})"
                )
            raise classify_error_response(response, rate_limited=limited, retry_after=retry_after)
        return response

    def _fetch_metadata(self, url: str) -> _Metadata:
        response = self._get(url, JSON_ACCEPT, MAX_METADATA_BYTES)
        if response.exceeded:
            raise VcsUpstreamError("Metadata response too large", retryable=False)
        try:
            data: Any = json.loads(response.body)
            head = data["head"]
            base = data["base"]
            head_sha, base_sha = head["sha"], base["sha"]
            title = data.get("title", "")
            state = data.get("state", "")
            changed = data.get("changed_files")
            head_ref, base_ref = head.get("ref", ""), base.get("ref", "")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise VcsUpstreamError("Malformed pull request metadata") from exc
        if (  # pylint: disable=too-many-boolean-expressions
            not isinstance(head_sha, str)
            or not isinstance(base_sha, str)
            or not head_sha
            or not base_sha
            or not isinstance(title, str)
            or not isinstance(state, str)
            or not isinstance(head_ref, str)
            or not isinstance(base_ref, str)
            or (changed is not None and (not isinstance(changed, int) or isinstance(changed, bool)))
        ):
            raise VcsUpstreamError("Malformed pull request metadata")
        return _Metadata(title, state, head_sha, base_sha, head_ref, base_ref, changed)

    def _fetch_diff(self, url: str) -> str:
        response = self._get(url, DIFF_ACCEPT, byte_ceiling(self._max_diff_tokens))
        return decode_diff(response, self._max_diff_tokens)
