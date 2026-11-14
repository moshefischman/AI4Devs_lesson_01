"""Collect sources to review from files, directories, and unified diffs."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .error import InputError

IGNORED_DIRS = frozenset(
    {
        ".git",
        "venv",
        ".venv",
        "node_modules",
        ".orchestrate",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

DEFAULT_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".go",
        ".rb",
        ".rs",
        ".cs",
        ".php",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".swift",
        ".scala",
        ".sql",
        ".sh",
        ".ps1",
        ".html",
        ".css",
        ".vue",
        ".svelte",
    }
)

_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SECRET_PREFIXES = ("id_rsa", "id_ecdsa", "id_ed25519")
_BINARY_PROBE_BYTES = 8192


@dataclass(frozen=True)
class Source:
    """One unit of input. `lines` keeps original 1-based line numbers."""

    path: str
    lines: tuple[tuple[int, str], ...]
    changed_lines: frozenset[int] = field(default_factory=frozenset)

    @property
    def line_numbers(self) -> frozenset[int]:
        return frozenset(number for number, _ in self.lines)


def is_secret_file(path: str | Path) -> bool:
    name = Path(path).name.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return name.endswith(_SECRET_SUFFIXES) or name.startswith(_SECRET_PREFIXES)


def _is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:_BINARY_PROBE_BYTES]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _numbered(text: str) -> tuple[tuple[int, str], ...]:
    return tuple(enumerate(text.splitlines(), start=1))


def _read_text(path: Path) -> str | None:
    raw = path.read_bytes()
    if _is_binary(raw):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def collect_file(path: str | Path, root: str | Path | None = None) -> Source:
    file_path = Path(path)
    root_path = Path(root) if root else Path.cwd()
    if not file_path.is_file():
        raise InputError(f"not a file: {path}")
    if is_secret_file(file_path):
        raise InputError(f"refusing to review a secret file: {file_path.name}")
    text = _read_text(file_path)
    if text is None:
        raise InputError(f"not a UTF-8 text file: {path}")
    return Source(path=_relative(file_path, root_path), lines=_numbered(text))


def _matches(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def collect_dir(
    path: str | Path,
    root: str | Path | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Source]:
    dir_path = Path(path)
    root_path = Path(root) if root else Path.cwd()
    if not dir_path.is_dir():
        raise InputError(f"not a directory: {path}")

    sources: list[Source] = []
    for current, dirnames, filenames in os.walk(dir_path):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
        for filename in sorted(filenames):
            file_path = Path(current) / filename
            relative = _relative(file_path, root_path)
            if is_secret_file(file_path):
                continue
            if include:
                if not _matches(relative, include):
                    continue
            elif file_path.suffix.lower() not in DEFAULT_EXTENSIONS:
                continue
            if exclude and _matches(relative, exclude):
                continue
            text = _read_text(file_path)
            if text is None or not text.strip():
                continue
            sources.append(Source(path=relative, lines=_numbered(text)))
    return sources


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(text: str) -> list[Source]:
    """Turn a unified diff into sources holding new-file lines (context + added)."""
    sources: list[Source] = []
    path: str | None = None
    lines: list[tuple[int, str]] = []
    changed: set[int] = set()
    next_line = 0
    in_hunk = False

    def flush() -> None:
        if path and lines and not is_secret_file(path):
            sources.append(Source(path=path, lines=tuple(lines), changed_lines=frozenset(changed)))

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            flush()
            path, lines, changed, in_hunk = None, [], set(), False
            continue
        if raw.startswith("+++ "):
            flush()
            target = raw[4:].strip()
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            lines, changed, in_hunk = [], set(), False
            continue
        if raw.startswith("--- ") and not in_hunk:
            continue
        header = _HUNK_HEADER.match(raw)
        if header:
            next_line = int(header.group(1))
            in_hunk = True
            continue
        if not in_hunk or path is None:
            continue
        if raw.startswith("+"):
            lines.append((next_line, raw[1:]))
            changed.add(next_line)
            next_line += 1
        elif raw.startswith(" "):
            lines.append((next_line, raw[1:]))
            next_line += 1
        elif raw == "":
            lines.append((next_line, ""))
            next_line += 1
        # "-" lines and "\ No newline at end of file" do not exist in the new file
    flush()
    return sources
