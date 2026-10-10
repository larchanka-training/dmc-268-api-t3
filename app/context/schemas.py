from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiffLine(ContextModel):
    kind: Literal["context", "addition", "deletion"]
    content: str
    old_line: int | None = Field(default=None, ge=1)
    new_line: int | None = Field(default=None, ge=1)
    no_final_newline: bool = False


class DiffHunk(ContextModel):
    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)
    section: str = ""
    lines: list[DiffLine] = Field(default_factory=list)


class DiffFile(ContextModel):
    old_path: str | None
    new_path: str | None
    change_type: Literal["addition", "deletion", "modification", "rename"]
    hunks: list[DiffHunk] = Field(default_factory=list)
    exclusion_reason: Literal["binary", "lockfile", "minified"] | None = None


class DiffContext(ContextModel):
    schema_version: Literal[1] = 1
    files: list[DiffFile] = Field(default_factory=list)
