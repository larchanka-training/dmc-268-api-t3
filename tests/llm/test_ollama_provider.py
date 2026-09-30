import json

import pytest
from pydantic import ValidationError

from app.llm.providers.ollama_provider import OllamaProvider


class _FakeOllamaClient:
    def __init__(self, response_text: str):
        self._response_text = response_text

    def generate(self, model, prompt, format):  # noqa: A002 - matches ollama.Client's signature
        return {"response": self._response_text}


def test_ollama_provider_returns_valid_review_result():
    payload = json.dumps(
        {
            "findings": [
                {
                    "file_path": "a.py",
                    "severity": "warning",
                    "message": "Unused import",
                }
            ]
        }
    )
    provider = OllamaProvider(client=_FakeOllamaClient(payload), model="llama3")

    result = provider.generate_structured("some prompt")

    assert len(result.findings) == 1
    assert result.findings[0].file_path == "a.py"


def test_ollama_provider_raises_on_malformed_json():
    provider = OllamaProvider(client=_FakeOllamaClient("not json"), model="llama3")

    with pytest.raises(json.JSONDecodeError):
        provider.generate_structured("some prompt")


def test_ollama_provider_raises_on_schema_mismatch():
    payload = json.dumps({"findings": [{"file_path": "a.py"}]})
    provider = OllamaProvider(client=_FakeOllamaClient(payload), model="llama3")

    with pytest.raises(ValidationError):
        provider.generate_structured("some prompt")
