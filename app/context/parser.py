import re
from typing import Literal

from app.context.schemas import DiffContext, DiffFile, DiffHunk, DiffLine

_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$")


class DiffParseError(ValueError):
    """The patch cannot be represented as complete unified-diff context."""


_NO_NEWLINE = "\\ No newline at end of file"
_SIMPLE_ESCAPES = {
    "a": 7,
    "b": 8,
    "f": 12,
    "n": 10,
    "r": 13,
    "t": 9,
    "v": 11,
    "\\": 92,
    '"': 34,
}
_LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "poetry.lock",
        "pipfile.lock",
        "uv.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
        "go.sum",
    }
)
_EXTENDED_HEADERS = (
    "index ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
)


def _unquote(text: str, start: int = 0) -> tuple[str, int]:
    """Decode one git C-style quoted token starting at text[start] == '"'."""
    data = bytearray()
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == '"':
            try:
                return data.decode("utf-8"), index + 1
            except UnicodeDecodeError:
                raise DiffParseError("Quoted path is not valid UTF-8") from None
        if char != "\\":
            try:
                data.extend(char.encode("utf-8"))
            except UnicodeEncodeError:
                raise DiffParseError("Invalid character in path") from None
            index += 1
            continue
        index += 1
        if index >= len(text):
            break
        escape = text[index]
        if escape in _SIMPLE_ESCAPES:
            data.append(_SIMPLE_ESCAPES[escape])
            index += 1
        elif escape in "0123" and all(c in "01234567" for c in text[index : index + 3]):
            if len(text[index : index + 3]) != 3:
                raise DiffParseError("Malformed quoted path escape")
            data.append(int(text[index : index + 3], 8))
            index += 3
        else:
            raise DiffParseError("Malformed quoted path escape")
    raise DiffParseError("Unterminated quoted path")


def _path_value(text: str) -> str:
    """Decode a whole path value that may be git-quoted."""
    if text.startswith('"'):
        value, end = _unquote(text)
        if end != len(text):
            raise DiffParseError("Trailing data after quoted path")
    else:
        value = text
    if not value:
        raise DiffParseError("Empty file path")
    return value


def _prefixed(value: str, prefix: str) -> str:
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise DiffParseError("Invalid file path header")
    return value[len(prefix) :]


def _marker_path(text: str, prefix: str) -> str | None:
    """Decode a ---/+++ value; None means /dev/null."""
    if not text.startswith('"') and text.endswith("\t"):
        text = text[:-1]
    if text == "/dev/null":
        return None
    return _prefixed(_path_value(text), prefix)


def _header_candidates(rest: str) -> list[tuple[str, str]]:
    """Possible (old, new) path pairs from a `diff --git` header (may be ambiguous)."""
    if rest.startswith('"'):
        old, end = _unquote(rest)
        if rest[end : end + 1] != " ":
            raise DiffParseError("Malformed file header")
        new = _path_value(rest[end + 1 :])
        return [(_prefixed(old, "a/"), _prefixed(new, "b/"))]
    candidates: list[tuple[str, str]] = []
    for index, char in enumerate(rest):
        left, right = rest[:index], rest[index + 1 :]
        if char != " " or not left.startswith("a/") or len(left) == 2:
            continue
        try:
            new = _path_value(right)
            candidates.append((left[2:], _prefixed(new, "b/")))
        except DiffParseError:
            continue
    if not candidates:
        raise DiffParseError("Malformed file header")
    return candidates


def _exclusion_reason(path: str) -> Literal["lockfile", "minified"] | None:
    name = path.rsplit("/", 1)[-1].lower()
    if name in _LOCKFILES:
        return "lockfile"
    if name.endswith((".min.js", ".min.mjs")):
        return "minified"
    return None


def _skip_binary_patch(lines: list[str], index: int) -> int:
    """Skip `GIT binary patch` sections without interpreting payload lines."""
    sections = 0
    while index < len(lines) and re.fullmatch(r"(literal|delta) \d+", lines[index]):
        index += 1
        while index < len(lines) and lines[index]:
            index += 1
        if index >= len(lines):
            raise DiffParseError("Incomplete binary patch")
        index += 1
        sections += 1
    if not sections:
        raise DiffParseError("Malformed binary patch")
    return index


def _hunk(lines: list[str], index: int) -> tuple[DiffHunk, int]:
    if lines[index].startswith("@@@"):
        raise DiffParseError("Combined diffs are not supported")
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
    old_closed = new_closed = False
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
        if (old_line is not None and old_closed) or (new_line is not None and new_closed):
            raise DiffParseError("Hunk line follows a no-final-newline marker")
        diff_line = DiffLine(kind=kind, content=line[1:], old_line=old_line, new_line=new_line)
        hunk.lines.append(diff_line)
        index += 1
        if index < len(lines) and lines[index].startswith("\\"):
            if lines[index] != _NO_NEWLINE:
                raise DiffParseError("Unsupported patch marker")
            diff_line.no_final_newline = True
            old_closed = old_closed or old_line is not None
            new_closed = new_closed or new_line is not None
            index += 1
    return hunk, index


def _file(lines: list[str], index: int) -> tuple[DiffFile, int]:
    header = lines[index]
    if header.startswith(("diff --cc ", "diff --combined ")):
        raise DiffParseError("Combined diffs are not supported")
    if not header.startswith("diff --git "):
        raise DiffParseError("Expected git file header")
    candidates = _header_candidates(header[len("diff --git ") :])
    index += 1
    is_new = is_deleted = is_binary = mode_change = False
    rename_from = rename_to = None
    binary_old_null = binary_new_null = False
    while index < len(lines):
        line = lines[index]
        if line.startswith(("Binary files ", "GIT binary patch")):
            if line == "GIT binary patch":
                index = _skip_binary_patch(lines, index + 1)
            elif line.endswith(" differ"):
                binary_old_null = line.startswith("Binary files /dev/null and ")
                binary_new_null = line.endswith(" and /dev/null differ")
                index += 1
            else:
                raise DiffParseError("Malformed binary file record")
            is_binary = True
            break
        if not line.startswith(_EXTENDED_HEADERS):
            if line.startswith(("copy from ", "copy to ")):
                raise DiffParseError("Copy patches are not supported")
            break
        if line.startswith("new file mode "):
            is_new = True
        elif line.startswith("deleted file mode "):
            is_deleted = True
        elif line.startswith(("old mode ", "new mode ")):
            mode_change = True
        elif line.startswith("rename from "):
            rename_from = _path_value(line[len("rename from ") :])
        elif line.startswith("rename to "):
            rename_to = _path_value(line[len("rename to ") :])
        index += 1
    is_new = is_new or binary_old_null
    is_deleted = is_deleted or binary_new_null
    if is_new and is_deleted:
        raise DiffParseError("File is both added and deleted")
    if (rename_from is None) != (rename_to is None):
        raise DiffParseError("Incomplete rename header")
    renamed = rename_from is not None and rename_to is not None

    has_content = not is_binary and index < len(lines) and lines[index].startswith("--- ")
    minus: str | None = None
    plus: str | None = None
    if has_content:
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise DiffParseError("Missing new file header")
        minus = _marker_path(lines[index][4:], "a/")
        plus = _marker_path(lines[index + 1][4:], "b/")
        if minus is None and plus is None:
            raise DiffParseError("Both file paths are null")
        index += 2

    old_path: str | None
    new_path: str | None
    if renamed:
        assert rename_from is not None and rename_to is not None
        old_path, new_path = rename_from, rename_to
        if old_path == new_path or (old_path, new_path) not in candidates:
            raise DiffParseError("File headers disagree")
        if has_content and (minus != old_path or plus != new_path):
            raise DiffParseError("File headers disagree")
    elif has_content:
        old_path, new_path = minus, plus
        if not any(
            (old_path is None or old_path == old) and (new_path is None or new_path == new)
            for old, new in candidates
        ):
            raise DiffParseError("File headers disagree")
        if (old_path is None) != is_new and (is_new or is_deleted):
            raise DiffParseError("File headers disagree")
        if (new_path is None) != is_deleted and (is_new or is_deleted):
            raise DiffParseError("File headers disagree")
    else:
        if not (is_binary or is_new or is_deleted or mode_change):
            raise DiffParseError("Missing file hunks")
        if is_new:
            names = {new for _, new in candidates}
        elif is_deleted:
            names = {old for old, _ in candidates}
        else:
            names = {old for old, new in candidates if old == new}
        if len(names) != 1:
            raise DiffParseError("File headers disagree")
        name = names.pop()
        old_path = None if is_new else name
        new_path = None if is_deleted else name

    change_type: Literal["addition", "deletion", "modification", "rename"] = (
        "rename"
        if renamed
        else "addition"
        if old_path is None
        else "deletion"
        if new_path is None
        else "modification"
    )
    file = DiffFile(old_path=old_path, new_path=new_path, change_type=change_type)
    if has_content:
        if index >= len(lines) or not lines[index].startswith("@@"):
            raise DiffParseError("Missing file hunks")
        while index < len(lines) and lines[index].startswith("@@"):
            hunk, index = _hunk(lines, index)
            if file.hunks:
                previous = file.hunks[-1]
                if hunk.old_start < previous.old_start + previous.old_count or (
                    hunk.new_start < previous.new_start + previous.new_count
                ):
                    raise DiffParseError("Overlapping or unordered hunk ranges")
            file.hunks.append(hunk)
    if is_binary:
        file.exclusion_reason = "binary"
    else:
        file.exclusion_reason = _exclusion_reason(new_path or old_path or "")
    if file.exclusion_reason is not None:
        file.hunks = []
    return file, index


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
        file, index = _file(lines, index)
        files.append(file)
    return DiffContext(files=files)
