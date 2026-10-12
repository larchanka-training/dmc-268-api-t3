import base64
import http.client
import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.webhooks.auth import verify_github, verify_gitlab
from app.webhooks.errors import WebhookAuthError
from scripts.replay_webhook import build_github_headers, build_gitlab_headers, main
from tests.api.conftest import (
    GITHUB_SECRET,
    GITLAB_SECRET_TOKEN,
    GITLAB_SIGNING_TOKEN,
    Api,
    fixture_bytes,
)

BODY = b'{"a": 1}\n'
NOW = 1_800_000_000.0
WRONG_GITHUB = "wrong-github-secret"
WRONG_GITLAB = "whsec_" + base64.b64encode(b"some-other-key").decode()


def _gitlab_ok(mode: str, headers: Mapping[str, str], now: float = NOW) -> None:
    verify_gitlab(
        mode,
        signing_token=GITLAB_SIGNING_TOKEN,
        secret_token=GITLAB_SECRET_TOKEN,
        body=BODY,
        headers=headers,
        now=now,
    )


def test_github_signature_matches_documented_vector():
    headers = build_github_headers(
        "It's a Secret to Everybody", b"Hello, World!", event="ping", delivery_id="d"
    )
    assert headers["X-Hub-Signature-256"] == (
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    )


def test_github_headers_authenticate_and_wrong_secret_fails():
    headers = build_github_headers(GITHUB_SECRET, BODY, event="pull_request", delivery_id="d1")
    verify_github(GITHUB_SECRET, BODY, headers)
    assert headers["X-GitHub-Event"] == "pull_request"
    assert headers["X-GitHub-Delivery"] == "d1"
    bad = build_github_headers(WRONG_GITHUB, BODY, event="pull_request", delivery_id="d1")
    with pytest.raises(WebhookAuthError):
        verify_github(GITHUB_SECRET, BODY, bad)


def test_gitlab_signed_headers_authenticate():
    headers = build_gitlab_headers(
        "signed", GITLAB_SIGNING_TOKEN, BODY, event="Merge Request Hook", delivery_id="g1", now=NOW
    )
    _gitlab_ok("signed", headers)
    assert headers["webhook-id"] == "g1"


def test_gitlab_signed_wrong_secret_fails():
    headers = build_gitlab_headers(
        "signed", WRONG_GITLAB, BODY, event="Merge Request Hook", delivery_id="g1", now=NOW
    )
    with pytest.raises(WebhookAuthError):
        _gitlab_ok("signed", headers)


@pytest.mark.parametrize(("offset", "accepted"), [(-299, True), (-301, False), (-600, False)])
def test_gitlab_signed_timestamp_offset(offset, accepted):
    headers = build_gitlab_headers(
        "signed",
        GITLAB_SIGNING_TOKEN,
        BODY,
        event="Merge Request Hook",
        delivery_id="g1",
        now=NOW,
        timestamp_offset=offset,
    )
    if accepted:
        _gitlab_ok("signed", headers)
    else:
        with pytest.raises(WebhookAuthError):
            _gitlab_ok("signed", headers)


def test_gitlab_token_headers_authenticate_and_wrong_token_fails():
    headers = build_gitlab_headers(
        "token", GITLAB_SECRET_TOKEN, BODY, event="Merge Request Hook", delivery_id="g1", now=NOW
    )
    _gitlab_ok("token", headers)
    assert headers["Idempotency-Key"] == "g1"
    bad = build_gitlab_headers(
        "token", "wrong-token", BODY, event="Merge Request Hook", delivery_id="g1", now=NOW
    )
    with pytest.raises(WebhookAuthError):
        _gitlab_ok("token", bad)


# --- main(): CLI behaviour ---------------------------------------------------------------


class Recorder:
    def __init__(self, status=200, response=b'{"status":"ok"}'):
        self.status = status
        self.response = response
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return self.status, self.response


def _payload(tmp_path: Path, body: bytes = BODY) -> str:
    path = tmp_path / "payload.json"
    path.write_bytes(body)
    return str(path)


def test_main_sends_exact_payload_bytes_with_valid_signature(tmp_path, capsys):
    body = b'{"x":  1,\r\n "y": "\xc3\xa9"}  '
    send = Recorder()
    code = main(
        ["github", "--url", "http://h/webhooks/github", "--payload", _payload(tmp_path, body)],
        env={"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET},
        send=send,
    )
    assert code == 0
    url, sent, headers = send.calls[0]
    assert url == "http://h/webhooks/github"
    assert sent == body
    assert headers["Content-Type"] == "application/json"
    verify_github(GITHUB_SECRET, body, headers)
    out = capsys.readouterr()
    assert "simulat" in out.out.lower()
    assert "not a live" in out.out.lower()
    sig = headers["X-Hub-Signature-256"]
    for text in (out.out, out.err):
        assert GITHUB_SECRET not in text
        assert sig not in text
        assert sig.split("=")[1] not in text


def test_main_default_secret_env_and_custom_secret_env(tmp_path):
    send = Recorder()
    env = {"GITLAB_WEBHOOK_SIGNING_TOKEN": GITLAB_SIGNING_TOKEN, "OTHER": WRONG_GITLAB}
    args = ["gitlab", "--url", "u", "--payload", _payload(tmp_path), "--delivery-id", "g9"]
    assert main(args, env=env, send=send, now=lambda: NOW) == 0
    _gitlab_ok("signed", send.calls[0][2])
    assert main([*args, "--secret-env", "OTHER"], env=env, send=send, now=lambda: NOW) == 0
    with pytest.raises(WebhookAuthError):
        _gitlab_ok("signed", send.calls[1][2])


def test_main_gitlab_token_mode_uses_token_env(tmp_path):
    send = Recorder()
    code = main(
        ["gitlab", "--url", "u", "--payload", _payload(tmp_path), "--gitlab-mode", "token"],
        env={"GITLAB_WEBHOOK_SECRET_TOKEN": GITLAB_SECRET_TOKEN},
        send=send,
    )
    assert code == 0
    _gitlab_ok("token", send.calls[0][2])


def test_main_unsigned_sends_no_authentication_and_needs_no_secret(tmp_path):
    send = Recorder()
    assert (
        main(
            ["github", "--url", "u", "--payload", _payload(tmp_path), "--unsigned"],
            env={},
            send=send,
        )
        == 0
    )
    headers = send.calls[0][2]
    with pytest.raises(WebhookAuthError):
        verify_github(GITHUB_SECRET, BODY, headers)
    assert "X-Hub-Signature-256" not in headers
    gitlab = Recorder()
    main(
        ["gitlab", "--url", "u", "--payload", _payload(tmp_path), "--unsigned"], env={}, send=gitlab
    )
    with pytest.raises(WebhookAuthError):
        _gitlab_ok("signed", gitlab.calls[0][2])


def test_main_negative_timestamp_offset_is_rejected_by_verifier(tmp_path):
    send = Recorder()
    main(
        ["gitlab", "--url", "u", "--payload", _payload(tmp_path), "--timestamp-offset", "-600"],
        env={"GITLAB_WEBHOOK_SIGNING_TOKEN": GITLAB_SIGNING_TOKEN},
        send=send,
        now=lambda: NOW,
    )
    with pytest.raises(WebhookAuthError):
        _gitlab_ok("signed", send.calls[0][2])


def test_main_missing_env_exits_2_without_sending(tmp_path, capsys):
    send = Recorder()
    code = main(["github", "--url", "u", "--payload", _payload(tmp_path)], env={}, send=send)
    assert code == 2
    assert send.calls == []
    assert "GITHUB_WEBHOOK_SECRET" in capsys.readouterr().err


def test_main_unreadable_file_exits_2(tmp_path, capsys):
    send = Recorder()
    code = main(
        ["github", "--url", "u", "--payload", str(tmp_path / "missing.json")],
        env={"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET},
        send=send,
    )
    assert code == 2
    assert send.calls == []
    captured = capsys.readouterr()
    assert GITHUB_SECRET not in captured.out + captured.err


def test_main_malformed_gitlab_signing_token_exits_2_without_leaking(tmp_path, capsys):
    secret = "not-a-whsec-secret-value"
    code = main(
        ["gitlab", "--url", "u", "--payload", _payload(tmp_path)],
        env={"GITLAB_WEBHOOK_SIGNING_TOKEN": secret},
        send=Recorder(),
    )
    assert code == 2
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_main_expectation_mismatch_exits_1(tmp_path):
    env = {"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET}
    base = ["github", "--url", "u", "--payload", _payload(tmp_path)]
    assert main(base, env=env, send=Recorder(status=401)) == 1
    assert main([*base, "--expect-status", "401"], env=env, send=Recorder(status=401)) == 0
    assert main([*base, "--expect", "ignored"], env=env, send=Recorder()) == 1
    ok = Recorder(response=b'{"status":"ignored"}')
    assert main([*base, "--expect", "ignored"], env=env, send=ok) == 0


@pytest.mark.parametrize("response_format", ["json", "status", "non_json"])
@pytest.mark.parametrize(
    "provider,mode", [("github", "signed"), ("gitlab", "signed"), ("gitlab", "token")]
)
def test_main_reflected_authentication_and_body_are_never_printed(
    tmp_path, capsys, provider, mode, response_format
):
    body = b'{"private": "confidential-payload-marker"}'
    env = {
        "GITHUB_WEBHOOK_SECRET": GITHUB_SECRET,
        "GITLAB_WEBHOOK_SIGNING_TOKEN": GITLAB_SIGNING_TOKEN,
        "GITLAB_WEBHOOK_SECRET_TOKEN": GITLAB_SECRET_TOKEN,
    }
    reflected_values = []

    def reflecting_send(url, sent, headers):
        authentication = headers.get(
            "X-Hub-Signature-256", headers.get("webhook-signature", headers.get("X-Gitlab-Token"))
        )
        reflected_values.extend([*env.values(), authentication, sent.decode()])
        reflection = " ".join(reflected_values)
        if response_format == "non_json":
            return 200, reflection.encode()
        if response_format == "status":
            return 200, json.dumps({"status": reflection}).encode()
        return 200, json.dumps({"status": "ignored", "reflection": reflection}).encode()

    code = main(
        [provider, "--url", "u", "--payload", _payload(tmp_path, body), "--gitlab-mode", mode],
        env=env,
        send=reflecting_send,
        now=lambda: NOW,
    )
    assert code == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for value in reflected_values:
        assert value not in output
    assert "confidential-payload-marker" not in output


def test_main_mismatch_diagnostic_does_not_echo_sensitive_expectation(tmp_path, capsys):
    code = main(
        [
            "gitlab",
            "--url",
            "u",
            "--payload",
            _payload(tmp_path),
            "--gitlab-mode",
            "token",
            "--expect",
            GITLAB_SECRET_TOKEN,
        ],
        env={"GITLAB_WEBHOOK_SECRET_TOKEN": GITLAB_SECRET_TOKEN},
        send=Recorder(response=b"not JSON"),
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "MISMATCH" in captured.err
    assert GITLAB_SECRET_TOKEN not in captured.out + captured.err


# --- in process against the real app -----------------------------------------------------


def _client_send(api: Api, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    response = api.client.post(url, content=body, headers=headers)
    return response.status_code, response.content


def test_signed_ping_is_ignored_by_the_app(api: Api, tmp_path):
    payload = _payload(tmp_path, fixture_bytes("github_ping.json"))
    code = main(
        [
            "github",
            "--url",
            "/webhooks/github",
            "--payload",
            payload,
            "--event",
            "ping",
            "--expect",
            "ignored",
        ],
        env={"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET},
        send=lambda u, b, h: _client_send(api, u, b, h),
    )
    assert code == 0


def test_wrong_secret_is_unauthorized_in_the_app(api: Api, tmp_path):
    payload = _payload(tmp_path, fixture_bytes("github_ping.json"))
    args = ["github", "--url", "/webhooks/github", "--payload", payload, "--event", "ping"]
    env = {"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET, "WRONG": WRONG_GITHUB}
    send = lambda u, b, h: _client_send(api, u, b, h)  # noqa: E731
    assert main([*args, "--secret-env", "WRONG", "--expect-status", "401"], env=env, send=send) == 0
    assert main([*args, "--unsigned", "--expect-status", "401"], env=env, send=send) == 0


def test_gitlab_signed_in_the_app_and_stale_timestamp_rejected(api: Api, tmp_path):
    payload = _payload(tmp_path, fixture_bytes("gitlab_merge_request_close.json"))
    args = ["gitlab", "--url", "/webhooks/gitlab", "--payload", payload]
    env = {"GITLAB_WEBHOOK_SIGNING_TOKEN": GITLAB_SIGNING_TOKEN}
    send = lambda u, b, h: _client_send(api, u, b, h)  # noqa: E731
    stale = [*args, "--timestamp-offset", "-600", "--expect-status", "401"]
    assert main(stale, env=env, send=send, now=time.time) == 0
    assert main(args, env=env, send=send, now=time.time) == 0


@pytest.mark.parametrize(
    "error", [ConnectionRefusedError("refused"), http.client.RemoteDisconnected("closed")]
)
def test_main_send_failure_exits_3_without_leaking(tmp_path, capsys, error):
    def failing_send(url, body, headers):
        raise error

    code = main(
        ["github", "--url", "u", "--payload", _payload(tmp_path)],
        env={"GITHUB_WEBHOOK_SECRET": GITHUB_SECRET},
        send=failing_send,
    )

    assert code == 3
    captured = capsys.readouterr()
    assert "request failed" in captured.err
    assert GITHUB_SECRET not in captured.out + captured.err
