import json

import httpx
import pytest

from app.llm.providers.eurorouter_provider import EurorouterProvider


def _valid_content() -> str:
    return json.dumps({"findings": [{"file_path": "a.py", "severity": "info", "message": "Note"}]})


def _chat_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def test_eurorouter_provider_succeeds_on_first_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer key-1"
        return httpx.Response(200, json=_chat_response(_valid_content()))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EurorouterProvider(
        base_url="https://eurorouter.example.com", api_keys=["key-1"], model="gpt-x", client=client
    )

    result = provider.generate_structured("prompt")

    assert len(result.findings) == 1


def test_eurorouter_provider_rotates_to_next_key_on_429():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        calls.append(auth)
        if auth == "Bearer key-1":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_chat_response(_valid_content()))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EurorouterProvider(
        base_url="https://eurorouter.example.com",
        api_keys=["key-1", "key-2"],
        model="gpt-x",
        client=client,
    )

    result = provider.generate_structured("prompt")

    assert len(result.findings) == 1
    assert calls == ["Bearer key-1", "Bearer key-2"]


def test_eurorouter_provider_raises_when_all_keys_fail():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EurorouterProvider(
        base_url="https://eurorouter.example.com",
        api_keys=["key-1", "key-2"],
        model="gpt-x",
        client=client,
    )

    with pytest.raises(RuntimeError):
        provider.generate_structured("prompt")


def test_eurorouter_provider_requires_at_least_one_key():
    with pytest.raises(ValueError):
        EurorouterProvider(base_url="https://eurorouter.example.com", api_keys=[], model="gpt-x")


def test_eurorouter_provider_rotates_to_next_key_on_malformed_response():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        calls.append(auth)
        if auth == "Bearer key-1":
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        return httpx.Response(200, json=_chat_response(_valid_content()))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EurorouterProvider(
        base_url="https://eurorouter.example.com",
        api_keys=["key-1", "key-2"],
        model="gpt-x",
        client=client,
    )

    result = provider.generate_structured("prompt")

    assert len(result.findings) == 1
    assert calls == ["Bearer key-1", "Bearer key-2"]
