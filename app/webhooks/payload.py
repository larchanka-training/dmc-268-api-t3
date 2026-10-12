"""Shared strict field validation for provider payloads."""

import json
import re
from collections.abc import Mapping
from typing import Any

from app.webhooks.errors import WebhookPayloadError

MAX_DELIVERY_ID = 255
MAX_REPOSITORY_ID = 255
MAX_REPOSITORY_NAME = 255
MAX_REPOSITORY_URL = 2048
MAX_TITLE = 500
MAX_SHORT = 255

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def storable(value: str) -> bool:
    """True when PostgreSQL text and UTF-8 can hold the value (no NUL, no lone surrogates)."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_object(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise WebhookPayloadError("payload is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise WebhookPayloadError("payload is not a JSON object")
    return parsed


def obj(parent: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise WebhookPayloadError(f"{path} must be an object")
    return value


def positive_int(parent: Mapping[str, Any], key: str, path: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WebhookPayloadError(f"{path} must be a positive integer")
    return value


def string(parent: Mapping[str, Any], key: str, path: str, max_length: int) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value or len(value) > max_length or not storable(value):
        raise WebhookPayloadError(f"{path} must be a non-empty string of at most {max_length}")
    return value


def optional_string(
    parent: Mapping[str, Any] | None, key: str, path: str, max_length: int
) -> str | None:
    if parent is None or parent.get(key) is None:
        return None
    value = parent[key]
    if not isinstance(value, str) or len(value) > max_length or not storable(value):
        raise WebhookPayloadError(f"{path} must be a string of at most {max_length}")
    return value or None


def sha(parent: Mapping[str, Any], key: str, path: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or _SHA.fullmatch(value.lower()) is None:
        raise WebhookPayloadError(f"{path} must be a 40 or 64 character hex object id")
    return value.lower()


def delivery_id(value: str | None, header: str) -> str:
    if not value or len(value) > MAX_DELIVERY_ID or not storable(value):
        raise WebhookPayloadError(f"{header} header is missing or invalid")
    return value
