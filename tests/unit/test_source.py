import pytest

from reviewer.error import InputError
from reviewer.source import collect_dir, collect_file, is_secret_file, parse_diff


def _write(path, text="x = 1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_collect_file_numbers_lines_and_uses_relative_path(tmp_path):
    _write(tmp_path / "src" / "app.py", "a = 1\nb = 2\n")
    source = collect_file(tmp_path / "src" / "app.py", root=tmp_path)
    assert source.path == "src/app.py"
    assert source.lines == ((1, "a = 1"), (2, "b = 2"))


def test_collect_file_refuses_env_file(tmp_path):
    _write(tmp_path / ".env", "TOKEN=abc\n")
    with pytest.raises(InputError, match="secret"):
        collect_file(tmp_path / ".env", root=tmp_path)


def test_collect_file_rejects_binary(tmp_path):
    (tmp_path / "blob.py").write_bytes(b"\x00\x01\x02")
    with pytest.raises(InputError, match="text"):
        collect_file(tmp_path / "blob.py", root=tmp_path)


def test_collect_file_rejects_missing_path(tmp_path):
    with pytest.raises(InputError):
        collect_file(tmp_path / "missing.py", root=tmp_path)


@pytest.mark.parametrize(
    "name,expected",
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        ("server.pem", True),
        ("private.key", True),
        ("id_rsa", True),
        (".env.example", False),
        ("environment.py", False),
    ],
)
def test_is_secret_file(name, expected):
    assert is_secret_file(name) is expected


def test_collect_dir_skips_ignored_dirs_binaries_and_secrets(tmp_path):
    _write(tmp_path / "app.py")
    _write(tmp_path / "pkg" / "util.ts")
    _write(tmp_path / "venv" / "lib.py")
    _write(tmp_path / ".git" / "hook.py")
    _write(tmp_path / "node_modules" / "dep.js")
    _write(tmp_path / ".env")
    _write(tmp_path / ".env.local")
    _write(tmp_path / "id_rsa")
    _write(tmp_path / "notes.txt")
    _write(tmp_path / "empty.py", "   \n")
    (tmp_path / "bin.py").write_bytes(b"\x00binary")

    paths = [s.path for s in collect_dir(tmp_path, root=tmp_path)]

    assert paths == ["app.py", "pkg/util.ts"]


def test_collect_dir_include_and_exclude_globs(tmp_path):
    _write(tmp_path / "a.py")
    _write(tmp_path / "b.py")
    _write(tmp_path / "notes.txt")

    included = collect_dir(tmp_path, root=tmp_path, include=["*.txt", "a.py"])
    excluded = collect_dir(tmp_path, root=tmp_path, exclude=["b.*"])

    assert [s.path for s in included] == ["a.py", "notes.txt"]
    assert [s.path for s in excluded] == ["a.py"]


def test_collect_dir_rejects_non_directory(tmp_path):
    with pytest.raises(InputError):
        collect_dir(tmp_path / "nope", root=tmp_path)


DIFF = """\
diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,4 +10,5 @@ def run():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     return a
@@ -40,2 +41,3 @@ def stop():
     x = 0
+    y = 1
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone = True
-also = False
diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1 +1 @@
-TOKEN=old
+TOKEN=new
"""


def test_parse_diff_keeps_new_file_line_numbers_and_changed_lines():
    sources = parse_diff(DIFF)

    assert [s.path for s in sources] == ["src/app.py"]
    source = sources[0]
    assert source.lines == (
        (10, "    a = 1"),
        (11, "    b = 3"),
        (12, "    c = 4"),
        (13, "    return a"),
        (41, "    x = 0"),
        (42, "    y = 1"),
    )
    assert source.changed_lines == frozenset({11, 12, 42})


def test_parse_diff_of_empty_text_returns_nothing():
    assert parse_diff("") == []
