import pytest

from app.llm.gateway import AllProvidersFailedError, LLMGateway
from app.llm.schemas import Finding, FindingSeverity, ReviewResult


class _FakeProvider:
    def __init__(
        self, name: str, result: ReviewResult | None = None, error: Exception | None = None
    ):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0

    def generate_structured(self, prompt: str) -> ReviewResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


_RESULT = ReviewResult(
    findings=[Finding(file_path="a.py", severity=FindingSeverity.INFO, message="Note")]
)


def test_gateway_returns_first_provider_result_on_success():
    provider = _FakeProvider("primary", result=_RESULT)
    gateway = LLMGateway([provider])

    result = gateway.generate_structured("prompt")

    assert result == _RESULT
    assert provider.calls == 1


def test_gateway_falls_back_to_second_provider_on_failure():
    primary = _FakeProvider("primary", error=RuntimeError("boom"))
    secondary = _FakeProvider("secondary", result=_RESULT)
    gateway = LLMGateway([primary, secondary])

    result = gateway.generate_structured("prompt")

    assert result == _RESULT
    assert primary.calls == 1
    assert secondary.calls == 1


def test_gateway_raises_when_all_providers_fail():
    primary = _FakeProvider("primary", error=RuntimeError("primary down"))
    secondary = _FakeProvider("secondary", error=RuntimeError("secondary down"))
    gateway = LLMGateway([primary, secondary])

    with pytest.raises(AllProvidersFailedError) as exc_info:
        gateway.generate_structured("prompt")

    assert "primary" in str(exc_info.value)
    assert "secondary" in str(exc_info.value)


def test_gateway_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        LLMGateway([])
