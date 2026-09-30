import pytest
from pydantic import ValidationError

from app.llm.schemas import Finding, FindingSeverity, ReviewResult


def test_finding_accepts_valid_data():
    finding = Finding(
        file_path="app/main.py",
        line_number=10,
        severity=FindingSeverity.WARNING,
        category="style",
        message="Unused import",
    )
    assert finding.severity == FindingSeverity.WARNING


def test_finding_allows_omitted_optional_fields():
    finding = Finding(file_path="app/main.py", severity=FindingSeverity.INFO, message="Note")
    assert finding.line_number is None
    assert finding.category is None


def test_finding_requires_file_path():
    with pytest.raises(ValidationError):
        Finding(severity=FindingSeverity.INFO, message="Note")


def test_finding_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        Finding(file_path="a.py", severity="not-a-severity", message="Note")


def test_finding_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Finding(
            file_path="a.py",
            severity=FindingSeverity.INFO,
            message="Note",
            unexpected="oops",
        )


def test_finding_rejects_blank_message():
    with pytest.raises(ValidationError):
        Finding(file_path="a.py", severity=FindingSeverity.INFO, message="   ")


def test_finding_rejects_non_positive_line_number():
    with pytest.raises(ValidationError):
        Finding(file_path="a.py", severity=FindingSeverity.INFO, message="Note", line_number=0)


def test_review_result_defaults_to_empty_findings():
    result = ReviewResult()
    assert result.findings == []


def test_review_result_accepts_multiple_findings():
    result = ReviewResult(
        findings=[
            Finding(file_path="a.py", severity=FindingSeverity.INFO, message="One"),
            Finding(file_path="b.py", severity=FindingSeverity.CRITICAL, message="Two"),
        ]
    )
    assert len(result.findings) == 2


def test_review_result_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ReviewResult(findings=[], unexpected="oops")
