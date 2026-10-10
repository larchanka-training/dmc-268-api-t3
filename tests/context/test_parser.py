import pytest

from app.context import DiffContext, DiffParseError, parse_diff


def patch(body: str, old: str = "a/code.py", new: str = "b/code.py") -> str:
    return f"diff --git a/code.py b/code.py\n--- {old}\n+++ {new}\n{body}"


def test_numbers_content_and_multiple_hunks():
    context = parse_diff(
        patch(
            "@@ -2,2 +2,3 @@ section\n same\n-old\n+new\n+extra\n"
            "@@ -10 +11 @@\n-last\n+next\n"
        )
    )
    file = context.files[0]
    assert file.change_type == "modification"
    assert file.old_path == file.new_path == "code.py"
    assert file.hunks[0].section == "section"
    assert [
        (line.kind, line.content, line.old_line, line.new_line)
        for line in file.hunks[0].lines
    ] == [
        ("context", "same", 2, 2),
        ("deletion", "old", 3, None),
        ("addition", "new", None, 3),
        ("addition", "extra", None, 4),
    ]
    assert file.hunks[1].lines[0].old_line == 10
    assert file.hunks[1].lines[1].new_line == 11
    assert DiffContext.model_validate_json(context.model_dump_json()) == context


def test_addition_deletion_and_multiple_files():
    context = parse_diff(
        patch("@@ -0,0 +1,2 @@\n+first\n+\n", old="/dev/null")
        + patch("@@ -1 +0,0 @@\n-gone\n", new="/dev/null")
    )
    assert [file.change_type for file in context.files] == ["addition", "deletion"]
    assert context.files[0].old_path is None
    assert context.files[0].hunks[0].lines[1].content == ""
    assert context.files[1].new_path is None


def test_empty_diff():
    assert parse_diff("") == DiffContext()


@pytest.mark.parametrize(
    "body",
    [
        "@@ -1,2 +1 @@\n-old\n+new\n",
        "@@ -1 +1 @@\n same\n+extra\n",
        "@@ -0 +1 @@\n-old\n+new\n",
        "@@ -1,-2 +1 @@\n-old\n+new\n",
        "@@ -1 +1 @@\n-old\n",
        "@@ -1 +1 @@\n-old\n+new",
        "+orphan\n",
        "@@ -2 +2 @@\n old\n@@ -2 +3 @@\n old\n",
        "@@ -2 +2 @@\n old\n@@ -3 +2 @@\n old\n",
    ],
)
def test_invalid_or_incomplete_patch_raises(body):
    with pytest.raises(DiffParseError):
        parse_diff(patch(body))


@pytest.mark.parametrize("raw", ["+orphan\n", "diff --git a/x b/x\n", "\n"])
def test_invalid_file_structure(raw):
    with pytest.raises(DiffParseError):
        parse_diff(raw)


def test_disagreeing_paths():
    with pytest.raises(DiffParseError, match="headers disagree"):
        parse_diff(patch("@@ -1 +1 @@\n same\n", old="a/another.py"))
