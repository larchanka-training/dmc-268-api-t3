import json
from pathlib import Path
from typing import Any

import pytest

from app.ingestion import VcsEvent
from app.webhooks.errors import WebhookPayloadError
from app.webhooks.events import IgnoredEvent
from app.webhooks.gitlab import normalize_gitlab

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "a" * 40


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def headers(**overrides) -> dict[str, str]:
    result = {"X-Gitlab-Event": "Merge Request Hook", "webhook-id": "wh-1"}
    result.update(overrides)
    return {k: v for k, v in result.items() if v is not None}


def payload(name: str = "gitlab_merge_request_open.json") -> dict[str, Any]:
    data: dict[str, Any] = json.loads(fixture(name))
    return data


def normalize_obj(data, **header_overrides):
    return normalize_gitlab(headers(**header_overrides), json.dumps(data).encode())


def test_open_normalizes_to_vcs_event():
    event = normalize_gitlab(headers(), fixture("gitlab_merge_request_open.json"))

    assert event == VcsEvent(
        provider="gitlab",
        delivery_id="wh-1",
        repository_external_id="123",
        pr_number=7,
        action="open",
        head_sha=HEAD,
        base_sha=None,
        repository_name="group/proj",
        repository_url="https://gitlab.com/group/proj",
        title="Fix bug",
        author="alice",
        source_branch="fix",
        target_branch="main",
    )


def test_update_with_changed_oldrev_is_supported():
    event = normalize_gitlab(headers(), fixture("gitlab_merge_request_update_with_oldrev.json"))

    assert isinstance(event, VcsEvent)
    assert event.action == "update"
    assert event.head_sha == HEAD


def test_header_lookup_is_case_insensitive():
    event = normalize_gitlab(
        {"x-gitlab-event": "Merge Request Hook", "WEBHOOK-ID": "wh-9"},
        fixture("gitlab_merge_request_open.json"),
    )

    assert isinstance(event, VcsEvent)
    assert event.delivery_id == "wh-9"


def test_delivery_id_falls_back_to_idempotency_key():
    event = normalize_gitlab(
        headers(**{"webhook-id": None, "Idempotency-Key": "idem-1"}),
        fixture("gitlab_merge_request_open.json"),
    )

    assert isinstance(event, VcsEvent)
    assert event.delivery_id == "idem-1"


def test_webhook_id_wins_over_idempotency_key():
    event = normalize_gitlab(
        headers(**{"Idempotency-Key": "idem-1"}), fixture("gitlab_merge_request_open.json")
    )

    assert isinstance(event, VcsEvent)
    assert event.delivery_id == "wh-1"


@pytest.mark.parametrize("empty", [None, "", "d" * 256])
def test_missing_empty_or_oversized_delivery_id_is_invalid(empty):
    with pytest.raises(WebhookPayloadError):
        normalize_gitlab(
            headers(**{"webhook-id": empty}), fixture("gitlab_merge_request_open.json")
        )


def test_author_is_none_when_actor_is_not_the_mr_author():
    data = payload()
    data["user"] = {"id": 99, "username": "someone-else"}
    event = normalize_obj(data)

    assert isinstance(event, VcsEvent)
    assert event.author is None


def test_author_is_none_when_user_missing():
    data = payload()
    del data["user"]
    event = normalize_obj(data)

    assert isinstance(event, VcsEvent)
    assert event.author is None


def test_sha_is_lowercased():
    data = payload()
    data["object_attributes"]["last_commit"]["id"] = "A" * 40
    event = normalize_obj(data)

    assert isinstance(event, VcsEvent)
    assert event.head_sha == "a" * 40


@pytest.mark.parametrize(
    "name",
    [
        "gitlab_merge_request_update_without_oldrev.json",
        "gitlab_merge_request_close.json",
    ],
)
def test_unsupported_fixtures_are_ignored(name):
    assert isinstance(normalize_gitlab(headers(), fixture(name)), IgnoredEvent)


@pytest.mark.parametrize(
    "action", ["reopen", "close", "merge", "approval", "approved", "unapproved"]
)
def test_unsupported_actions_are_ignored(action):
    data = payload()
    data["object_attributes"]["action"] = action

    assert isinstance(normalize_obj(data), IgnoredEvent)


@pytest.mark.parametrize("oldrev", [None, "", HEAD])
def test_update_without_source_commit_change_is_ignored(oldrev):
    data = payload("gitlab_merge_request_update_with_oldrev.json")
    data["object_attributes"]["oldrev"] = oldrev

    assert isinstance(normalize_obj(data), IgnoredEvent)


def test_oldrev_comparison_is_case_insensitive():
    data = payload("gitlab_merge_request_update_with_oldrev.json")
    data["object_attributes"]["oldrev"] = HEAD.upper()

    assert isinstance(normalize_obj(data), IgnoredEvent)


@pytest.mark.parametrize("event", ["Push Hook", "Note Hook", "Pipeline Hook", None])
def test_other_hook_types_are_ignored(event):
    result = normalize_gitlab(headers(**{"X-Gitlab-Event": event}), b"not json")

    assert isinstance(result, IgnoredEvent)


def test_other_object_kind_is_ignored():
    data = payload()
    data["object_kind"] = "push"

    assert isinstance(normalize_obj(data), IgnoredEvent)


def test_unsupported_action_with_otherwise_invalid_body_is_ignored():
    assert isinstance(
        normalize_obj({"object_kind": "merge_request", "object_attributes": {"action": "close"}}),
        IgnoredEvent,
    )


@pytest.mark.parametrize("body", [b"", b"{nope", b"\xff", b"[]", b"null", b'"s"'])
def test_malformed_or_non_object_body_is_invalid(body):
    with pytest.raises(WebhookPayloadError):
        normalize_gitlab(headers(), body)


def with_attr(key, value):
    data = payload()
    data["object_attributes"][key] = value
    return data


def with_project(key, value):
    data = payload()
    data["project"][key] = value
    return data


def without(*path):
    data = payload()
    node = data
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return data


INVALID = {
    "missing object_attributes": without("object_attributes"),
    "missing project": without("project"),
    "missing project id": without("project", "id"),
    "project id string": with_project("id", "123"),
    "project id zero": with_project("id", 0),
    "project id bool": with_project("id", True),
    "project id negative": with_project("id", -1),
    "missing iid": without("object_attributes", "iid"),
    "iid string": with_attr("iid", "7"),
    "iid zero": with_attr("iid", 0),
    "iid bool": with_attr("iid", True),
    "missing last_commit": without("object_attributes", "last_commit"),
    "missing head id": without("object_attributes", "last_commit", "id"),
    "bad sha": with_attr("last_commit", {"id": "xyz"}),
    "sha not string": with_attr("last_commit", {"id": 12}),
    "title too long": with_attr("title", "t" * 501),
    "title not string": with_attr("title", 1),
    "title with NUL": with_attr("title", "a\u0000b"),
    "title with lone surrogate": with_attr("title", "a\ud800b"),
    "branch with NUL": with_attr("source_branch", "fe\u0000at"),
    "name with NUL": with_project("path_with_namespace", "g/pr\u0000oj"),
    "url with lone surrogate": with_project("web_url", "https://x/\udc00"),
    "branch too long": with_attr("source_branch", "b" * 256),
    "url too long": with_project("web_url", "https://x/" + "u" * 2048),
    "name too long": with_project("path_with_namespace", "n" * 256),
    "project not object": {**payload(), "project": []},
}


@pytest.mark.parametrize("data", list(INVALID.values()), ids=list(INVALID))
def test_invalid_supported_payload_is_rejected(data):
    with pytest.raises(WebhookPayloadError):
        normalize_obj(data)


def test_non_string_oldrev_on_update_is_ignored():
    data = payload("gitlab_merge_request_update_with_oldrev.json")
    data["object_attributes"]["oldrev"] = 5

    assert isinstance(normalize_obj(data), IgnoredEvent)


def test_payload_error_messages_do_not_echo_payload_values():
    data = payload()
    data["object_attributes"]["last_commit"]["id"] = "SECRET-LOOKING-VALUE"
    with pytest.raises(WebhookPayloadError) as info:
        normalize_obj(data)

    assert "SECRET-LOOKING-VALUE" not in str(info.value)
