# The two adapters intentionally mirror each other behind the shared VcsClient port.
# pylint: disable=duplicate-code
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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

PROVIDER = "gitlab"
DEFAULT_MAX_DIFF_TOKENS = 32000
# Documented flags that mean the provider omitted or limited file diffs.
OVERFLOW_FLAGS = ("overflow", "too_large", "diff_overflow")


@dataclass(frozen=True)
class _Metadata:  # pylint: disable=too-many-instance-attributes
    title: str
    state: str
    sha: str
    base_sha: str
    head_sha: str
    start_sha: str
    source_branch: str
    target_branch: str
    changes_count: int

    @property
    def revision(self) -> tuple[str, str, str, str]:
        return (self.sha, self.base_sha, self.head_sha, self.start_sha)


class GitLabClient:
    """Retrieves MR metadata plus the complete raw diff for one consistent revision.

    ``PullRequestRef.repository`` is the numeric project id as text. No internal
    retries: errors carry ``retryable`` for the caller's policy.

    Completeness: ``GET .../raw_diffs`` has no pagination and no overflow flag; the
    docs only say MRs exceeding diff limits "return limited results". The only
    provable signal is the MR's ``changes_count`` (a string, ``"1000+"`` when capped),
    which must be numeric and equal to the number of ``diff --git`` headers.
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
            raise VcsInvalidRequestError("GitLabClient requires an absolute http(s) base URL")
        if not token:
            raise VcsInvalidRequestError("GitLabClient requires a token")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._max_diff_tokens = max_diff_tokens
        self._client = client or httpx.Client()
        self._clock = clock

    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot:
        url = self._mr_url(ref)
        expected = ref.expected_head_sha.strip().lower()
        if not expected:
            raise VcsInvalidRequestError("expected_head_sha is required")

        first = self._fetch_metadata(url)
        if first.sha.lower() != expected:
            raise VcsRevisionChangedError("Merge request head differs from the expected revision")
        if first.head_sha != first.sha:
            # diff_refs trail the source branch while GitLab computes the new diff version.
            raise VcsUpstreamError("Merge request diff is not yet computed for the head revision")

        raw_diff = self._fetch_diff(f"{url}/raw_diffs")

        second = self._fetch_metadata(url)
        if second.revision != first.revision:
            raise VcsRevisionChangedError("Merge request revision changed during retrieval")

        # Each file section starts with "diff --git" at line start; content lines are
        # always prefixed (+, -, space), so this cannot match diff body text.
        header_count = sum(1 for line in raw_diff.split("\n") if line.startswith("diff --git "))
        if header_count != first.changes_count:
            raise VcsDiffIncompleteError(
                f"Diff has {header_count} files but metadata reports {first.changes_count}"
            )

        return RevisionSnapshot(
            provider=PROVIDER,
            repository=ref.repository,
            number=ref.number,
            title=first.title,
            state=first.state,
            head_sha=first.sha,
            base_sha=first.base_sha,
            head_ref=first.source_branch,
            base_ref=first.target_branch,
            raw_diff=raw_diff,
        )

    def _mr_url(self, ref: PullRequestRef) -> str:
        if not (ref.repository.isascii() and ref.repository.isdigit()):
            raise VcsInvalidRequestError("repository must be a numeric GitLab project id")
        if ref.number <= 0:
            raise VcsInvalidRequestError("merge request iid must be positive")
        return f"{self._base_url}/projects/{int(ref.repository)}/merge_requests/{ref.number}"

    def _headers(self) -> dict[str, str]:
        return {
            "PRIVATE-TOKEN": self._token,
            "Accept": "application/json, text/plain;q=0.9",
            "User-Agent": "dmc-268-vcs-client",
        }

    def _rate_limit_retry_after(self, response: BoundedResponse) -> float | None:
        # Documented: throttled responses are 429 with Retry-After (seconds) and
        # RateLimit-Reset (epoch seconds).
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None:
            reset = response.headers.get("ratelimit-reset")
            if reset is not None and reset.isdigit():
                retry_after = max(0.0, float(reset) - self._clock())
        return retry_after

    def _get(self, url: str, max_bytes: int) -> BoundedResponse:
        response = get_bounded(
            self._client,
            url,
            headers=self._headers(),
            timeout=self._timeout,
            max_bytes=max_bytes,
        )
        if not 200 <= response.status_code < 300:
            limited = response.status_code == 429
            retry_after = self._rate_limit_retry_after(response) if limited else None
            raise classify_error_response(response, rate_limited=limited, retry_after=retry_after)
        return response

    def _fetch_metadata(  # pylint: disable=too-many-locals
        self, url: str
    ) -> _Metadata:
        response = self._get(url, MAX_METADATA_BYTES)
        if response.exceeded:
            raise VcsUpstreamError("Metadata response too large", retryable=False)
        try:
            data: Any = json.loads(response.body)
            sha = data["sha"]
            refs = data.get("diff_refs")
            title = data.get("title", "")
            state = data.get("state", "")
            source = data.get("source_branch", "")
            target = data.get("target_branch", "")
            changes = data.get("changes_count")
            flagged = any(data.get(flag) is True for flag in OVERFLOW_FLAGS)
            if not isinstance(refs, dict):
                refs = {}
            base_sha, head_sha, start_sha = (
                refs.get("base_sha"),
                refs.get("head_sha"),
                refs.get("start_sha"),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise VcsUpstreamError("Malformed merge request metadata") from exc
        if (
            not isinstance(sha, str)
            or not sha
            or not all(isinstance(v, str) for v in (title, state, source, target))
        ):
            raise VcsUpstreamError("Malformed merge request metadata")
        if flagged:
            raise VcsDiffIncompleteError("Provider reports the diff overflowed its limits")
        if not (base_sha and head_sha and start_sha) or not all(
            isinstance(v, str) for v in (base_sha, head_sha, start_sha)
        ):
            # Empty until GitLab populates the diff asynchronously.
            raise VcsUpstreamError("Merge request diff refs are not available yet")
        count = self._parse_changes_count(changes)
        return _Metadata(title, state, sha, base_sha, head_sha, start_sha, source, target, count)

    @staticmethod
    def _parse_changes_count(value: object) -> int:
        if isinstance(value, int) and not isinstance(value, bool):
            text = str(value)
        elif isinstance(value, str):
            text = value.strip()
        else:
            text = ""
        if text.endswith("+"):
            raise VcsDiffIncompleteError("Merge request exceeds the provider change limit")
        if not text.isascii() or not text.isdigit():
            # Empty or non-numeric: completeness cannot be proven, fail closed.
            raise VcsDiffIncompleteError("Merge request change count is unavailable")
        return int(text)

    def _fetch_diff(self, url: str) -> str:
        response = self._get(url, byte_ceiling(self._max_diff_tokens))
        return decode_diff(response, self._max_diff_tokens)
