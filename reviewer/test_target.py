"""Describe a Python module so Claude can write tests for it."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

from .error import InputError
from .source import IGNORED_DIRS, collect_file

# Module-level names that signal a dependency tests must fake or freeze.
_EXTERNAL_HINTS = {
    "network": ("requests", "httpx", "httpx2", "urllib", "socket", "aiohttp", "anthropic"),
    "filesystem": ("open", "pathlib", "shutil", "os.remove", "os.path"),
    "time": ("time", "datetime"),
    "randomness": ("random", "uuid", "secrets"),
    "subprocess": ("subprocess",),
}


@dataclass(frozen=True)
class TargetCallable:
    name: str
    signature: str
    doc: str | None
    owner: str | None = None  # class name for methods

    @property
    def qualified_name(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name


@dataclass(frozen=True)
class TestTarget:
    __test__ = False

    path: str
    module: str
    source_text: str
    callables: tuple[TargetCallable, ...]
    external: tuple[str, ...] = ()
    existing_tests: dict[str, str] = field(default_factory=dict)

    @property
    def test_file_name(self) -> str:
        return f"test_{Path(self.path).stem}.py"


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name in {"__init__", "__call__"})


def public_callables(tree: ast.Module) -> list[TargetCallable]:
    found: list[TargetCallable] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _public(node.name):
            found.append(TargetCallable(node.name, _signature(node), ast.get_docstring(node)))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and _public(item.name):
                    found.append(
                        TargetCallable(
                            item.name, _signature(item), ast.get_docstring(item), node.name
                        )
                    )
    return found


def external_dependencies(tree: ast.Module) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    found = []
    for kind, hints in _EXTERNAL_HINTS.items():
        if any(name == hint or name.startswith(f"{hint}.") for name in names for hint in hints):
            found.append(kind)
    return found


def module_name(relative_path: str) -> str:
    parts = list(Path(relative_path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
        raise InputError(f"cannot import {relative_path} as a module: path is not a dotted name")
    return ".".join(parts)


def find_existing_tests(root: Path, stem: str) -> dict[str, str]:
    """Return {relative path: text} for files named test_<stem>.py under root."""
    wanted = f"test_{stem}.py"
    found: dict[str, str] = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
        if wanted in filenames:
            path = Path(current) / wanted
            found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return found


def analyze(path: str | Path, root: str | Path) -> TestTarget:
    root_path = Path(root).resolve()
    file_path = Path(path).resolve()
    if file_path.suffix != ".py":
        raise InputError(f"test generation supports Python files only: {path}")
    try:
        file_path.relative_to(root_path)
    except ValueError as exc:
        raise InputError(f"{path} is outside the project root {root}") from exc

    source = collect_file(file_path, root=root_path)
    text = "\n".join(line for _, line in source.lines)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise InputError(f"cannot parse {source.path}: {exc.msg} (line {exc.lineno})") from exc

    callables = public_callables(tree)
    if not callables:
        raise InputError(f"{source.path} has no public function or method to test")

    return TestTarget(
        path=source.path,
        module=module_name(source.path),
        source_text=text,
        callables=tuple(callables),
        external=tuple(external_dependencies(tree)),
        existing_tests=find_existing_tests(root_path, file_path.stem),
    )
