from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PullRequestRef:
    """Identity of a PR/MR taken from a verified webhook.

    ``repository`` is GitHub ``owner/name`` (GitLab: numeric project id as text).
    ``expected_head_sha`` is the source revision the delivery announced.
    """

    provider: str
    repository: str
    number: int
    expected_head_sha: str


@dataclass(frozen=True)
class RevisionSnapshot:
    provider: str
    repository: str
    number: int
    title: str
    state: str
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    raw_diff: str


class VcsClient(Protocol):
    def fetch_revision(self, ref: PullRequestRef) -> RevisionSnapshot: ...
