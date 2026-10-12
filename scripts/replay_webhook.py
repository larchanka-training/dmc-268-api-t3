"""Replay a webhook payload with locally built provider authentication headers.

Development tool, stdlib only, not shipped in the production image. The signing value is read
from an environment variable (never a command-line value) and is never printed. A run is a
simulated delivery signed locally, not a live check of a real provider.
"""

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3

_KEY_PREFIX = "whsec_"
_DEFAULT_EVENTS = {"github": "pull_request", "gitlab": "Merge Request Hook"}
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_PRINTED_RESPONSE = 200

Send = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]


class UsageError(Exception):
    """Invalid usage or configuration; the message never contains secret values."""


def build_github_headers(
    secret: str, body: bytes, *, event: str, delivery_id: str
) -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": f"sha256={digest}",
    }


def build_gitlab_headers(
    mode: str,
    secret: str,
    body: bytes,
    *,
    event: str,
    delivery_id: str,
    now: float,
    timestamp_offset: int = 0,
) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "X-Gitlab-Event": event}
    if mode == "token":
        headers["Idempotency-Key"] = delivery_id
        headers["X-Gitlab-Token"] = secret
        return headers
    if mode != "signed":
        raise UsageError("unknown GitLab mode")
    if not secret.startswith(_KEY_PREFIX):
        raise UsageError("the GitLab signing token must start with whsec_")
    try:
        key = base64.b64decode(secret[len(_KEY_PREFIX) :], validate=True)
    except ValueError as exc:
        raise UsageError("the GitLab signing token is not valid base64") from exc
    timestamp = str(int(now) + timestamp_offset)
    signed = f"{delivery_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    headers["webhook-id"] = delivery_id
    headers["webhook-timestamp"] = timestamp
    headers["webhook-signature"] = f"v1,{signature}"
    return headers


def _unsigned_headers(provider: str, mode: str, event: str, delivery_id: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider == "github":
        headers["X-GitHub-Event"] = event
        headers["X-GitHub-Delivery"] = delivery_id
    else:
        headers["X-Gitlab-Event"] = event
        headers["Idempotency-Key" if mode == "token" else "webhook-id"] = delivery_id
    return headers


def default_secret_env(provider: str, mode: str) -> str:
    if provider == "github":
        return "GITHUB_WEBHOOK_SECRET"
    return "GITLAB_WEBHOOK_SECRET_TOKEN" if mode == "token" else "GITLAB_WEBHOOK_SIGNING_TOKEN"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay_webhook.py",
        description=(
            "Send a payload file to a webhook endpoint with locally built authentication "
            "headers. This is a simulated delivery, not a live provider check. The secret is "
            "read from an environment variable and never printed. Exit codes: 0 ok, "
            "1 response mismatch, 2 usage or configuration error, 3 network/transport failure."
        ),
    )
    parser.add_argument("provider", choices=["github", "gitlab"])
    parser.add_argument("--url", required=True, help="full webhook URL")
    parser.add_argument("--payload", required=True, help="file whose exact bytes are sent")
    parser.add_argument("--event", help="event header value (provider-specific default)")
    parser.add_argument("--delivery-id", help="delivery id (default: random uuid4)")
    parser.add_argument("--gitlab-mode", choices=["signed", "token"], default="signed")
    parser.add_argument(
        "--secret-env",
        metavar="NAME",
        help="environment variable holding the secret (default: the backend's variable name)",
    )
    parser.add_argument(
        "--unsigned", action="store_true", help="send without authentication headers"
    )
    parser.add_argument(
        "--timestamp-offset",
        type=int,
        default=0,
        metavar="SECONDS",
        help="shift the GitLab signed timestamp (negative = in the past)",
    )
    parser.add_argument("--expect-status", type=int, default=200, metavar="N")
    parser.add_argument("--expect", metavar="STATUS", help='expected JSON "status" value')
    return parser


def _urllib_send(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), bytes(exc.read())


def _read_payload(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read payload file: {exc.strerror}") from exc


def _headers_for(
    args: argparse.Namespace, body: bytes, delivery_id: str, env: Mapping[str, str], now: float
) -> dict[str, str]:
    event = args.event or _DEFAULT_EVENTS[args.provider]
    if args.unsigned:
        return _unsigned_headers(args.provider, args.gitlab_mode, event, delivery_id)
    name = args.secret_env or default_secret_env(args.provider, args.gitlab_mode)
    secret = env.get(name)
    if not secret:
        raise UsageError(f"environment variable {name} is not set or empty")
    if args.provider == "github":
        return build_github_headers(secret, body, event=event, delivery_id=delivery_id)
    return build_gitlab_headers(
        args.gitlab_mode,
        secret,
        body,
        event=event,
        delivery_id=delivery_id,
        now=now,
        timestamp_offset=args.timestamp_offset,
    )


def _response_status(raw: bytes) -> str | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    value = data.get("status") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def main(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    send: Send = _urllib_send,
    now: Callable[[], float] = time.time,
) -> int:
    args = _parser().parse_args(argv)
    environment = os.environ if env is None else env
    delivery_id = args.delivery_id or str(uuid.uuid4())
    try:
        body = _read_payload(args.payload)
        headers = _headers_for(args, body, delivery_id, environment, now())
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print("SIMULATED delivery signed locally; this is not a live provider check.")
    try:
        status, raw = send(args.url, body, headers)
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        print(f"error: request failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_TRANSPORT
    shown = raw[:_MAX_PRINTED_RESPONSE].decode("utf-8", errors="replace")
    print(f"delivery_id={delivery_id} http_status={status} response={shown}")
    if status != args.expect_status:
        print(f"MISMATCH: expected http status {args.expect_status}", file=sys.stderr)
        return EXIT_MISMATCH
    if args.expect is not None and _response_status(raw) != args.expect:
        print(f"MISMATCH: expected response status {args.expect}", file=sys.stderr)
        return EXIT_MISMATCH
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
