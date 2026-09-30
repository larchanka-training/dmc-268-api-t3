from app.llm.gateway import LLMGateway
from app.llm.review import generate_review
from app.llm.schemas import Finding, FindingSeverity, ReviewResult

_RESULT = ReviewResult(
    findings=[Finding(file_path="a.py", severity=FindingSeverity.WARNING, message="Note")]
)


class _RecordingProvider:
    name = "recording"

    def __init__(self):
        self.last_prompt: str | None = None

    def generate_structured(self, prompt: str) -> ReviewResult:
        self.last_prompt = prompt
        return _RESULT


def test_generate_review_returns_gateway_result_and_includes_diff_in_prompt():
    provider = _RecordingProvider()
    gateway = LLMGateway([provider])

    result = generate_review(gateway, diff="diff --git a b", language="python")

    assert result == _RESULT
    assert provider.last_prompt is not None
    assert "diff --git a b" in provider.last_prompt
    assert "python" in provider.last_prompt
