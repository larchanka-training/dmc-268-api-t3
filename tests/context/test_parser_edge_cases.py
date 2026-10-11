from pathlib import Path

import pytest

from app.context import DiffParseError, parse_diff

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return parse_diff((FIXTURES / name).read_text(encoding="utf-8"))


def test_quoted_non_ascii_and_escaped_paths():
    first, second = load("quoted_nonascii.diff").files
    assert first.old_path == first.new_path == "café 日本.py"
    assert first.change_type == "modification"
    assert len(first.hunks) == 1
    assert second.new_path == 'tab\there "q".txt'


def test_rename_only_has_paths_and_no_hunks():
    spaced, quoted = load("rename_only.diff").files
    assert (spaced.change_type, spaced.old_path, spaced.new_path) == (
        "rename",
        "old name.py",
        "new name.py",
    )
    assert spaced.hunks == [] and spaced.exclusion_reason is None
    assert (quoted.old_path, quoted.new_path) == ("ä.py", "plain.py")


def test_rename_with_changes_keeps_hunks():
    (file,) = load("rename_with_changes.diff").files
    assert file.change_type == "rename"
    assert (file.old_path, file.new_path) == ("src/old.py", "src/new.py")
    assert [line.kind for line in file.hunks[0].lines] == ["context", "deletion", "addition"]


def test_no_newline_markers_attach_to_preceding_line_only():
    both, new_only, none = load("no_newline.diff").files
    flags = [(line.kind, line.no_final_newline) for line in both.hunks[0].lines]
    assert flags == [
        ("context", False),
        ("deletion", True),
        ("addition", True),
    ]
    assert [line.content for line in both.hunks[0].lines] == ["same", "last", "last"]
    assert [line.no_final_newline for line in new_only.hunks[0].lines] == [False, False, True]
    assert not any(line.no_final_newline for line in none.hunks[0].lines)


def test_excluded_files_keep_paths_and_reasons_without_hunks():
    files = load("excluded.diff").files
    summary = [(f.new_path or f.old_path, f.exclusion_reason, f.hunks) for f in files]
    assert summary[:5] == [
        ("image.png", "binary", []),
        ("data.bin", "binary", []),
        ("web/package-lock.json", "lockfile", []),
        ("static/app.min.js", "minified", []),
        ("static/mod.min.mjs", "minified", []),
    ]
    assert files[1].change_type == "addition" and files[1].old_path is None
    assert files[4].change_type == "deletion" and files[4].new_path is None
    assert files[5].exclusion_reason is None and len(files[5].hunks) == 1


def test_literal_patch_metadata_inside_hunk_is_content():
    doc, following = load("literal_metadata.diff").files
    assert [line.content for line in doc.hunks[0].lines] == [
        "context",
        "--- a/x",
        "+++ b/x",
        "diff --git a/y b/y",
        "@@ -1 +1 @@",
        "tail",
    ]
    assert [line.kind for line in doc.hunks[0].lines][1:3] == ["deletion", "addition"]
    assert following.change_type == "addition"


def test_empty_new_file_and_mode_change_have_no_hunks():
    context = parse_diff(
        "diff --git a/empty.txt b/empty.txt\nnew file mode 100644\nindex 0000000..e69de29\n"
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
    )
    assert [(f.change_type, f.hunks) for f in context.files] == [
        ("addition", []),
        ("modification", []),
    ]


def test_combined_diff_fixture_rejected():
    with pytest.raises(DiffParseError, match="Combined"):
        load("combined.diff")


@pytest.mark.parametrize(
    "raw",
    [
        "diff --combined merged.py\n",
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@@ -1 -1 +1 @@@\n  c\n",
        'diff --git "a/unterminated b/x\n--- "a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n',
        'diff --git "a/bad\\q.py" "b/bad\\q.py"\n--- "a/bad\\q.py"\n+++ "b/bad\\q.py"\n'
        "@@ -1 +1 @@\n-a\n+b\n",
        'diff --git "a/\\303(.py" "b/\\303(.py"\n--- "a/\\303(.py"\n+++ "b/\\303(.py"\n'
        "@@ -1 +1 @@\n-a\n+b\n",
        'diff --git "a/x.py"junk "b/x.py"\n',
        "diff --git a/x b/y\nsimilarity index 100%\nrename from x\n",
        "diff --git a/x b/y\ncopy from x\ncopy to y\n",
        "diff --git a/x b/x\nGIT binary patch\nliteral 3\nabc\n",
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-a\n\\ No newline at end of file\n"
        "-b\n+c\n",
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n\\ Something else\n+b\n",
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n"
        "\\ No newline at end of file\n",
    ],
)
def test_unsupported_or_malformed_patches_raise(raw):
    with pytest.raises(DiffParseError):
        parse_diff(raw)
