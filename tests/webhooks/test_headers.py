from app.webhooks.headers import get_header


def test_lookup_is_case_insensitive():
    assert get_header({"x-github-event": "ping"}, "X-GitHub-Event") == "ping"


def test_missing_header_is_none():
    assert get_header({}, "X-GitHub-Event") is None
