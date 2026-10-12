"""GitLab merge request webhook validation and normalization."""

from collections.abc import Mapping

from app.ingestion.events import VcsEvent
from app.webhooks import payload as p
from app.webhooks.events import IgnoredEvent
from app.webhooks.headers import get_header


def _delivery(headers: Mapping[str, str]) -> str:
    value = get_header(headers, "webhook-id") or get_header(headers, "Idempotency-Key")
    return p.delivery_id(value, "webhook-id/Idempotency-Key")


def _author(data: Mapping[str, object], attrs: Mapping[str, object]) -> str | None:
    """The top-level ``user`` is the actor; it is the author only when ids match."""
    user = data.get("user")
    author_id = attrs.get("author_id")
    if not isinstance(user, dict) or isinstance(author_id, bool) or author_id is None:
        return None
    if isinstance(user.get("id"), bool) or user.get("id") != author_id:
        return None
    return p.optional_string(user, "username", "user.username", p.MAX_SHORT)


def normalize_gitlab(headers: Mapping[str, str], body: bytes) -> VcsEvent | IgnoredEvent:
    if get_header(headers, "X-Gitlab-Event") != "Merge Request Hook":
        return IgnoredEvent("unsupported event")
    data = p.parse_object(body)
    if data.get("object_kind") != "merge_request":
        return IgnoredEvent("unsupported object kind")
    attrs = p.obj(data, "object_attributes", "object_attributes")
    action = attrs.get("action")
    if action not in ("open", "update"):
        return IgnoredEvent("unsupported action")
    oldrev = attrs.get("oldrev")
    if action == "update" and (not isinstance(oldrev, str) or not oldrev):
        return IgnoredEvent("update without source commit change")

    last_commit = p.obj(attrs, "last_commit", "object_attributes.last_commit")
    head_sha = p.sha(last_commit, "id", "object_attributes.last_commit.id")
    if action == "update" and isinstance(oldrev, str) and oldrev.lower() == head_sha:
        return IgnoredEvent("update without source commit change")

    delivery = _delivery(headers)
    project = p.obj(data, "project", "project")
    project_id = p.positive_int(project, "id", "project.id")
    return VcsEvent(
        provider="gitlab",
        delivery_id=delivery,
        repository_external_id=str(project_id),
        pr_number=p.positive_int(attrs, "iid", "object_attributes.iid"),
        action=action,
        head_sha=head_sha,
        base_sha=None,
        repository_name=p.optional_string(
            project, "path_with_namespace", "project.path_with_namespace", p.MAX_REPOSITORY_NAME
        ),
        repository_url=p.optional_string(
            project, "web_url", "project.web_url", p.MAX_REPOSITORY_URL
        ),
        title=p.optional_string(attrs, "title", "object_attributes.title", p.MAX_TITLE),
        author=_author(data, attrs),
        source_branch=p.optional_string(
            attrs, "source_branch", "object_attributes.source_branch", p.MAX_SHORT
        ),
        target_branch=p.optional_string(
            attrs, "target_branch", "object_attributes.target_branch", p.MAX_SHORT
        ),
    )
