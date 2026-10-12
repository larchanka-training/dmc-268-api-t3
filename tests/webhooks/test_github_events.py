import json
from typing import Any
from pathlib import Path

import pytest

from app.ingestion import VcsEvent
from app.webhooks.errors import WebhookPayloadError
from app.webhooks.events import IgnoredEvent
from app.webhooks.github import normalize_github

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "a" * 40
BASE = "b" * 40


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def headers(event: str = "pull_request", delivery: str | None = "gh-delivery-1") -> dict[str, str]:
    result = {"X-GitHub-Event": event}
    if delivery is not None:
        result["X-GitHub-Delivery"] = delivery
    return result


def opened(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(fixture("github_pull_request_opened.json"))
    for key, value in overrides.items():
        payload[key] = value
    return payload


def normalize_obj(payload, **header_kwargs):
    return normalize_github(headers(**header_kwargs), json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("github_pull_request_opened.json", "opened"),
        ("github_pull_request_synchronize.json", "synchronize"),
    ],
)
def test_supported_actions_normalize_to_vcs_event(name, action):
    event = normalize_github(headers(), fixture(name))

    assert event == VcsEvent(
        provider="github",
        delivery_id="gh-delivery-1",
        repository_external_id="octo/repo",
        pr_number=42,
        action=action,
        head_sha=HEAD,
        base_sha=BASE,
        repository_name="octo/repo",
        repository_url="https://github.com/octo/repo",
        title="Add feature",
        author="octocat",
        source_branch="feature",
        target_branch="main",
    )


def test_header_lookup_is_case_insensitive():
    event = normalize_github(
        {"x-github-event": "pull_request", "x-github-delivery": "d-2"},
        fixture("github_pull_request_opened.json"),
    )

    assert isinstance(event, VcsEvent)
    assert event.delivery_id == "d-2"


def test_sha_is_lowercased():
    payload = opened()
    payload["pull_request"]["head"]["sha"] = "A" * 40
    event = normalize_obj(payload)

    assert isinstance(event, VcsEvent)
    assert event.head_sha == "a" * 40


def test_sha256_object_ids_are_accepted():
    payload = opened()
    payload["pull_request"]["head"]["sha"] = "1" * 64
    event = normalize_obj(payload)

    assert isinstance(event, VcsEvent)
    assert event.head_sha == "1" * 64


def test_ping_is_ignored_without_payload_validation():
    result = normalize_github(headers("ping"), b"not json at all")

    assert isinstance(result, IgnoredEvent)


def test_other_event_type_is_ignored():
    assert isinstance(normalize_github(headers("issues"), b"[]"), IgnoredEvent)


def test_missing_event_header_is_ignored():
    assert isinstance(normalize_github({}, b"{}"), IgnoredEvent)


def test_unsupported_action_is_ignored():
    result = normalize_github(headers(), fixture("github_pull_request_closed.json"))

    assert isinstance(result, IgnoredEvent)


def test_unsupported_action_with_otherwise_invalid_body_is_ignored():
    result = normalize_obj({"action": "labeled", "number": "nope"})

    assert isinstance(result, IgnoredEvent)


@pytest.mark.parametrize("body", [b"", b"{not json", b"\xff\xfe", b"[]", b'"x"', b"null"])
def test_malformed_or_non_object_body_for_pull_request_is_invalid(body):
    with pytest.raises(WebhookPayloadError):
        normalize_github(headers(), body)


def test_missing_action_is_invalid():
    payload = opened()
    del payload["action"]
    with pytest.raises(WebhookPayloadError):
        normalize_obj(payload)


def drop(path: tuple[str, ...]):
    payload = opened()
    node = payload
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return payload


def setv(path: tuple[str, ...], value):
    payload = opened()
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return payload


INVALID_PAYLOADS = {
    "missing number": drop(("number",)),
    "missing head sha": drop(("pull_request", "head", "sha")),
    "missing base sha": drop(("pull_request", "base", "sha")),
    "missing repository": drop(("repository",)),
    "missing full_name": drop(("repository", "full_name")),
    "number zero": setv(("number",), 0),
    "number negative": setv(("number",), -3),
    "number bool": setv(("number",), True),
    "number string": setv(("number",), "42"),
    "number float": setv(("number",), 4.2),
    "sha too short": setv(("pull_request", "head", "sha"), "abc123"),
    "sha non hex": setv(("pull_request", "head", "sha"), "g" * 40),
    "sha not string": setv(("pull_request", "head", "sha"), 123),
    "base sha bad": setv(("pull_request", "base", "sha"), "z" * 40),
    "full_name one segment": setv(("repository", "full_name"), "octo"),
    "full_name three segments": setv(("repository", "full_name"), "a/b/c"),
    "full_name empty segment": setv(("repository", "full_name"), "octo/"),
    "full_name dot": setv(("repository", "full_name"), "octo/."),
    "full_name dotdot": setv(("repository", "full_name"), "../repo"),
    "full_name not string": setv(("repository", "full_name"), 7),
    "pull_request not object": setv(("pull_request",), "x"),
    "title too long": setv(("pull_request", "title"), "t" * 501),
    "title not string": setv(("pull_request", "title"), 5),
    "url too long": setv(("repository", "html_url"), "https://x/" + "u" * 2048),
    "branch too long": setv(("pull_request", "head", "ref"), "b" * 256),
    "author too long": setv(("pull_request", "user", "login"), "a" * 256),
    "full_name too long": setv(("repository", "full_name"), "o/" + "r" * 255),
}


@pytest.mark.parametrize("payload", list(INVALID_PAYLOADS.values()), ids=list(INVALID_PAYLOADS))
def test_invalid_supported_payload_is_rejected(payload):
    with pytest.raises(WebhookPayloadError):
        normalize_obj(payload)


@pytest.mark.parametrize("delivery", [None, "", "d" * 256])
def test_missing_or_oversized_delivery_id_is_invalid(delivery):
    with pytest.raises(WebhookPayloadError):
        normalize_obj(opened(), delivery=delivery)


def test_optional_metadata_may_be_absent():
    payload = opened()
    del payload["pull_request"]["title"]
    del payload["pull_request"]["user"]
    del payload["repository"]["html_url"]
    event = normalize_obj(payload)

    assert isinstance(event, VcsEvent)
    assert event.title is None
    assert event.author is None
    assert event.repository_url is None


def test_payload_error_messages_do_not_echo_payload_values():
    payload = opened()
    payload["pull_request"]["head"]["sha"] = "SECRET-LOOKING-VALUE"
    with pytest.raises(WebhookPayloadError) as info:
        normalize_obj(payload)

    assert "SECRET-LOOKING-VALUE" not in str(info.value)
