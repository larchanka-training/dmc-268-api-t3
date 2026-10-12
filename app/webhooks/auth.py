"""Provider webhook authentication over raw request bytes (framework-free)."""

import base64
import binascii
import hashlib
import hmac
import logging
import re
from collections.abc import Mapping

from app.webhooks.errors import WebhookAuthError
from app.webhooks.headers import get_header

logger = logging.getLogger(__name__)

GITLAB_TIMESTAMP_TOLERANCE_SECONDS = 300
_GITHUB_PREFIX = "sha256="
_GITHUB_DIGEST = re.compile(r"[0-9a-fA-F]{64}")
_GITLAB_KEY_PREFIX = "whsec_"


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _config_error(provider: str, problem: str) -> WebhookAuthError:
    logger.error(
        "Webhook authentication is misconfigured: provider=%s problem=%s", provider, problem
    )
    return WebhookAuthError("webhook authentication failed")


def verify_github(secret: str, body: bytes, headers: Mapping[str, str]) -> None:
    if not secret:
        raise _config_error("github", "empty secret")
    header = get_header(headers, "X-Hub-Signature-256")
    if header is None or not header.startswith(_GITHUB_PREFIX):
        raise WebhookAuthError("webhook authentication failed")
    digest = header[len(_GITHUB_PREFIX) :]
    if _GITHUB_DIGEST.fullmatch(digest) is None:
        raise WebhookAuthError("webhook authentication failed")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not _equal(expected, digest.lower()):
        raise WebhookAuthError("webhook authentication failed")


def _decode_signing_key(signing_token: str) -> bytes:
    if not signing_token.startswith(_GITLAB_KEY_PREFIX):
        raise _config_error("gitlab", "signing token without whsec_ prefix")
    try:
        key = base64.b64decode(signing_token[len(_GITLAB_KEY_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _config_error("gitlab", "signing token is not base64") from exc
    if not key:
        raise _config_error("gitlab", "empty signing key")
    return key


def _verify_gitlab_signed(
    signing_token: str, body: bytes, headers: Mapping[str, str], now: float
) -> None:
    key = _decode_signing_key(signing_token)
    msg_id = get_header(headers, "webhook-id")
    timestamp = get_header(headers, "webhook-timestamp")
    signature = get_header(headers, "webhook-signature")
    if not msg_id or timestamp is None or not signature:
        raise WebhookAuthError("webhook authentication failed")
    if re.fullmatch(r"[0-9]{1,15}", timestamp) is None:
        raise WebhookAuthError("webhook authentication failed")
    if abs(now - int(timestamp)) > GITLAB_TIMESTAMP_TOLERANCE_SECONDS:
        raise WebhookAuthError("webhook authentication failed")
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    matched = False
    for entry in signature.split():
        version, _, value = entry.partition(",")
        if version != "v1":
            continue
        # Evaluate every entry; no early exit on the first match.
        if hmac.compare_digest(value.encode("utf-8"), expected):
            matched = True
    if not matched:
        raise WebhookAuthError("webhook authentication failed")


def _verify_gitlab_token(secret_token: str, headers: Mapping[str, str]) -> None:
    if not secret_token:
        raise _config_error("gitlab", "empty secret token")
    received = get_header(headers, "X-Gitlab-Token")
    if received is None or not _equal(secret_token, received):
        raise WebhookAuthError("webhook authentication failed")


def verify_gitlab(  # pylint: disable=too-many-arguments
    mode: str,
    *,
    signing_token: str,
    secret_token: str,
    body: bytes,
    headers: Mapping[str, str],
    now: float,
) -> None:
    if mode == "signed":
        _verify_gitlab_signed(signing_token, body, headers, now)
    elif mode == "token":
        _verify_gitlab_token(secret_token, headers)
    else:
        raise _config_error("gitlab", "unknown auth mode")
