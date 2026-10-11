import re
from typing import Literal

from app.context.schemas import DiffContext, DiffFile, DiffHunk, DiffLine

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$")


class DiffParseError(ValueError):
    """The patch cannot be represented as complete unified-diff context."""


def _path(value: str, prefix: str) -> str | None:
    if value == "/dev/null":
        return None
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise DiffParseError("Invalid file path header")
    return value[len(prefix) :]


def _hunk(lines: list[str], index: int) -> tuple[DiffHunk, int]:
    match = _HUNK.fullmatch(lines[index])
    if match is None:
        raise DiffParseError("Malformed hunk header")
    old_start, old_count, new_start, new_count = (
        int(match[1]),
        int(match[2]) if match[2] is not None else 1,
        int(match[3]),
        int(match[4]) if match[4] is not None else 1,
    )
    if (old_count and not old_start) or (new_count and not new_start):
        raise DiffParseError("Nonempty range starts at zero")
    hunk = DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        section=match[5] or "",
    )
    old_used = new_used = 0
    index += 1
    while old_used < old_count or new_used < new_count:
        if index >= len(lines) or not lines[index] or lines[index][0] not in " +-":
            raise DiffParseError("Incomplete hunk")
        line = lines[index]
        marker = line[0]
        kind: Literal["context", "addition", "deletion"] = (
            "context" if marker == " " else "addition" if marker == "+" else "deletion"
        )
        old_line = old_start + old_used if marker != "+" else None
        new_line = new_start + new_used if marker != "-" else None
        old_used += marker != "+"
        new_used += marker != "-"
        if old_used > old_count or new_used > new_count:
            raise DiffParseError("Hunk lines exceed declared ranges")
        hunk.lines.append(
            DiffLine(kind=kind, content=line[1:], old_line=old_line, new_line=new_line)
        )
        index += 1
    return hunk, index


def parse_diff(raw_diff: str) -> DiffContext:
    """Parse a complete git unified diff without I/O or partial success."""
    if not raw_diff:
        return DiffContext()
    if not raw_diff.endswith("\n"):
        raise DiffParseError("Incomplete patch line")
    lines = raw_diff.split("\n")[:-1]
    files: list[DiffFile] = []
    index = 0
    while index < len(lines):
        header = lines[index]
        if not header.startswith("diff --git a/") or " b/" not in header:
            raise DiffParseError("Expected git file header")
        header_old, header_new = header[len("diff --git ") :].split(" b/", 1)
        expected_old = _path(header_old, "a/")
        expected_new = _path("b/" + header_new, "b/")
        index += 1
        while index < len(lines) and lines[index].startswith(
            ("index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ")
        ):
            index += 1
        if index + 1 >= len(lines) or not lines[index].startswith("--- "):
            raise DiffParseError("Missing old file header")
        old_path = _path(lines[index][4:], "a/")
        index += 1
        if not lines[index].startswith("+++ "):
            raise DiffParseError("Missing new file header")
        new_path = _path(lines[index][4:], "b/")
        if (old_path is not None and old_path != expected_old) or (
            new_path is not None and new_path != expected_new
        ):
            raise DiffParseError("File headers disagree")
        if old_path is None and new_path is None:
            raise DiffParseError("Both file paths are null")
        file = DiffFile(
            old_path=old_path,
            new_path=new_path,
            change_type=(
                "addition"
                if old_path is None
                else "deletion"
                if new_path is None
                else "modification"
            ),
        )
        index += 1
        while index < len(lines) and lines[index].startswith("@@"):
            hunk, index = _hunk(lines, index)
            if file.hunks:
                previous = file.hunks[-1]
                if hunk.old_start < previous.old_start + previous.old_count or (
                    hunk.new_start < previous.new_start + previous.new_count
                ):
                    raise DiffParseError("Overlapping or unordered hunk ranges")
            file.hunks.append(hunk)
        if not file.hunks:
            raise DiffParseError("Missing file hunks")
        files.append(file)
    return DiffContext(files=files)
