"""GitHub ``pull_request`` webhook validation and normalization."""

from collections.abc import Mapping

from app.ingestion.events import VcsEvent
from app.webhooks import payload as p
from app.webhooks.errors import WebhookPayloadError
from app.webhooks.events import IgnoredEvent
from app.webhooks.headers import get_header

SUPPORTED_ACTIONS = frozenset({"opened", "synchronize"})


def _full_name(parent: Mapping[str, object]) -> str:
    value = p.string(parent, "full_name", "repository.full_name", p.MAX_REPOSITORY_ID)
    parts = value.split("/")
    if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
        raise WebhookPayloadError("repository.full_name must be owner/name")
    return value


def normalize_github(headers: Mapping[str, str], body: bytes) -> VcsEvent | IgnoredEvent:
    event_name = get_header(headers, "X-GitHub-Event")
    if event_name != "pull_request":
        return IgnoredEvent("unsupported event")
    data = p.parse_object(body)
    action = data.get("action")
    if not isinstance(action, str) or not action:
        raise WebhookPayloadError("action must be a non-empty string")
    if action not in SUPPORTED_ACTIONS:
        return IgnoredEvent("unsupported action")

    delivery = p.delivery_id(get_header(headers, "X-GitHub-Delivery"), "X-GitHub-Delivery")
    number = p.positive_int(data, "number", "number")
    pull_request = p.obj(data, "pull_request", "pull_request")
    head = p.obj(pull_request, "head", "pull_request.head")
    base = p.obj(pull_request, "base", "pull_request.base")
    repository = p.obj(data, "repository", "repository")
    user = pull_request.get("user")
    if user is not None and not isinstance(user, dict):
        raise WebhookPayloadError("pull_request.user must be an object")

    full_name = _full_name(repository)
    return VcsEvent(
        provider="github",
        delivery_id=delivery,
        repository_external_id=full_name,
        pr_number=number,
        action=action,
        head_sha=p.sha(head, "sha", "pull_request.head.sha"),
        base_sha=p.sha(base, "sha", "pull_request.base.sha"),
        repository_name=full_name,
        repository_url=p.optional_string(
            repository, "html_url", "repository.html_url", p.MAX_REPOSITORY_URL
        ),
        title=p.optional_string(pull_request, "title", "pull_request.title", p.MAX_TITLE),
        author=p.optional_string(user, "login", "pull_request.user.login", p.MAX_SHORT),
        source_branch=p.optional_string(head, "ref", "pull_request.head.ref", p.MAX_SHORT),
        target_branch=p.optional_string(base, "ref", "pull_request.base.ref", p.MAX_SHORT),
    )
