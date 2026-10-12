import base64
import hashlib
import hmac
import logging

import pytest

from app.webhooks.auth import verify_github, verify_gitlab
from app.webhooks.errors import WebhookAuthError

GITHUB_SECRET = "It's a Secret to Everybody"
GITHUB_BODY = b"Hello, World!"
GITHUB_VECTOR = "757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"


def gh_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_documented_reference_vector_authenticates():
    verify_github(GITHUB_SECRET, GITHUB_BODY, {"X-Hub-Signature-256": "sha256=" + GITHUB_VECTOR})


def test_github_header_lookup_is_case_insensitive():
    verify_github(GITHUB_SECRET, GITHUB_BODY, {"x-hub-signature-256": "sha256=" + GITHUB_VECTOR})


@pytest.mark.parametrize(
    ("secret", "body", "header"),
    [
        ("wrong", GITHUB_BODY, "sha256=" + GITHUB_VECTOR),
        (GITHUB_SECRET, b"Hello, World?", "sha256=" + GITHUB_VECTOR),
        (GITHUB_SECRET, GITHUB_BODY, GITHUB_VECTOR),
        (GITHUB_SECRET, GITHUB_BODY, "sha1=" + GITHUB_VECTOR),
        (GITHUB_SECRET, GITHUB_BODY, "sha256=" + GITHUB_VECTOR[:-2]),
        (GITHUB_SECRET, GITHUB_BODY, "sha256=" + "z" * 64),
        (GITHUB_SECRET, GITHUB_BODY, "sha256=" + "é" * 64),
        (GITHUB_SECRET, GITHUB_BODY, ""),
        ("", GITHUB_BODY, gh_sig("", GITHUB_BODY)),
    ],
)
def test_github_rejects_invalid_signatures(secret, body, header):
    with pytest.raises(WebhookAuthError):
        verify_github(secret, body, {"X-Hub-Signature-256": header})


def test_github_rejects_missing_header():
    with pytest.raises(WebhookAuthError):
        verify_github(GITHUB_SECRET, GITHUB_BODY, {})


def test_github_errors_do_not_contain_secret_or_signature(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WebhookAuthError) as info:
        verify_github("topsecret-value", GITHUB_BODY, {"X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert "topsecret-value" not in str(info.value)
    assert "0" * 64 not in str(info.value)
    assert "topsecret-value" not in caplog.text


# ---- GitLab ----

KEY = b"gitlab-test-key-0123456789abcdef"
SIGNING_TOKEN = "whsec_" + base64.b64encode(KEY).decode()
NOW = 1_800_000_000.0
BODY = b'{"object_kind": "merge_request"}'


def gl_sig(msg_id: str, ts: str, body: bytes, key: bytes = KEY) -> str:
    mac = hmac.new(key, msg_id.encode() + b"." + ts.encode() + b"." + body, hashlib.sha256)
    return "v1," + base64.b64encode(mac.digest()).decode()


def signed_headers(ts: int | None = None, msg_id: str = "msg-1", body: bytes = BODY, **extra):
    ts_text = str(int(NOW) if ts is None else ts)
    headers = {
        "webhook-id": msg_id,
        "webhook-timestamp": ts_text,
        "webhook-signature": gl_sig(msg_id, ts_text, body),
    }
    headers.update(extra)
    return headers


def verify_signed(headers, body=BODY, signing_token=SIGNING_TOKEN, now=NOW):
    verify_gitlab(
        "signed",
        signing_token=signing_token,
        secret_token="",
        body=body,
        headers=headers,
        now=now,
    )


def test_gitlab_signed_valid_authenticates():
    verify_signed(signed_headers())


def test_gitlab_signed_headers_case_insensitive():
    headers = {k.title(): v for k, v in signed_headers().items()}
    verify_signed(headers)


def test_gitlab_signed_accepts_when_one_of_several_entries_matches():
    headers = signed_headers()
    other = gl_sig(
        "msg-1", headers["webhook-timestamp"], BODY, key=b"another-key-another-key-1234567"
    )
    headers["webhook-signature"] = f"{other} v1,AAAA v2,zzz {headers['webhook-signature']}"
    verify_signed(headers)


@pytest.mark.parametrize("offset", [300, -300])
def test_gitlab_signed_accepts_timestamp_on_tolerance_boundary(offset):
    verify_signed(signed_headers(ts=int(NOW) + offset))


@pytest.mark.parametrize("offset", [301, -301, 86400, -86400])
def test_gitlab_signed_rejects_stale_or_future_timestamp(offset):
    with pytest.raises(WebhookAuthError):
        verify_signed(signed_headers(ts=int(NOW) + offset))


def test_gitlab_signed_rejects_non_integer_timestamp():
    headers = signed_headers()
    headers["webhook-timestamp"] = "1.8e9"
    headers["webhook-signature"] = gl_sig("msg-1", "1.8e9", BODY)
    with pytest.raises(WebhookAuthError):
        verify_signed(headers)


@pytest.mark.parametrize("missing", ["webhook-id", "webhook-timestamp", "webhook-signature"])
def test_gitlab_signed_rejects_missing_header(missing):
    headers = signed_headers()
    del headers[missing]
    with pytest.raises(WebhookAuthError):
        verify_signed(headers)


def test_gitlab_signed_rejects_signature_over_different_id_or_timestamp():
    base = signed_headers()
    other_id = dict(base, **{"webhook-id": "msg-2"})
    other_ts = dict(base, **{"webhook-timestamp": str(int(NOW) + 1)})
    for headers in (other_id, other_ts):
        with pytest.raises(WebhookAuthError):
            verify_signed(headers)


def test_gitlab_signed_rejects_tampered_body():
    with pytest.raises(WebhookAuthError):
        verify_signed(signed_headers(), body=BODY + b" ")


@pytest.mark.parametrize(
    "token", ["", "whsec_", "whsec_!!!not-base64!!!", "no-prefix-" + base64.b64encode(KEY).decode()]
)
def test_gitlab_signed_rejects_malformed_or_empty_signing_token(token):
    with pytest.raises(WebhookAuthError):
        verify_signed(signed_headers(), signing_token=token)


def test_gitlab_signed_rejects_valid_secret_token_without_signature():
    with pytest.raises(WebhookAuthError):
        verify_gitlab(
            "signed",
            signing_token=SIGNING_TOKEN,
            secret_token="plain",
            body=BODY,
            headers={"X-Gitlab-Token": "plain"},
            now=NOW,
        )


def test_gitlab_signed_rejects_bad_signature_even_with_valid_secret_token():
    headers = signed_headers(body=b"other")
    headers["X-Gitlab-Token"] = "plain"
    with pytest.raises(WebhookAuthError):
        verify_gitlab(
            "signed",
            signing_token=SIGNING_TOKEN,
            secret_token="plain",
            body=BODY,
            headers=headers,
            now=NOW,
        )


def test_gitlab_signed_rejects_non_ascii_signature_header():
    headers = signed_headers()
    headers["webhook-signature"] = "v1,é"
    with pytest.raises(WebhookAuthError):
        verify_signed(headers)


def verify_token(headers, secret_token="plain-token"):
    verify_gitlab(
        "token",
        signing_token="",
        secret_token=secret_token,
        body=BODY,
        headers=headers,
        now=NOW,
    )


def test_gitlab_token_mode_accepts_matching_token_without_signature_headers():
    verify_token({"X-Gitlab-Token": "plain-token"})


@pytest.mark.parametrize("value", ["wrong", "plain-tokeñ", "plain-token "])
def test_gitlab_token_mode_rejects_wrong_or_non_ascii_token(value):
    with pytest.raises(WebhookAuthError):
        verify_token({"X-Gitlab-Token": value})


def test_gitlab_token_mode_rejects_missing_token():
    with pytest.raises(WebhookAuthError):
        verify_token({})


def test_gitlab_token_mode_rejects_empty_configured_token():
    with pytest.raises(WebhookAuthError):
        verify_token({"X-Gitlab-Token": ""}, secret_token="")


def test_gitlab_token_mode_ignores_stale_signature_headers():
    verify_token(dict(signed_headers(ts=1), **{"X-Gitlab-Token": "plain-token"}))


@pytest.mark.parametrize("mode", ["", "none", "SIGNED", "both"])
def test_gitlab_unknown_mode_fails_closed(mode):
    with pytest.raises(WebhookAuthError):
        verify_gitlab(
            mode,
            signing_token=SIGNING_TOKEN,
            secret_token="plain-token",
            body=BODY,
            headers=dict(signed_headers(), **{"X-Gitlab-Token": "plain-token"}),
            now=NOW,
        )


def test_gitlab_errors_and_logs_do_not_leak_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WebhookAuthError) as info:
        verify_gitlab(
            "token",
            signing_token="whsec_leak-signing",
            secret_token="leak-secret-token",
            body=BODY,
            headers={"X-Gitlab-Token": "leak-guess"},
            now=NOW,
        )
    with pytest.raises(WebhookAuthError) as info2:
        verify_signed(signed_headers(ts=1), signing_token=SIGNING_TOKEN)
    for text in (str(info.value), str(info2.value), caplog.text):
        for leaked in ("leak-secret-token", "leak-guess", "leak-signing", SIGNING_TOKEN):
            assert leaked not in text
