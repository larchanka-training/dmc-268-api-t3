from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FindingSeverity(str, Enum):  # noqa: UP042 (str mixin required for JSON-serializable enum)
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(min_length=1)
    line_number: int | None = Field(default=None, ge=1)
    severity: FindingSeverity
    category: str | None = None
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)
