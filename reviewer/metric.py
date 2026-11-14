"""Static metrics for Python files: lint (ruff) and cyclomatic complexity (radon)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from radon.complexity import cc_visit

from .model import ComplexityResult, LintResult

COMPLEXITY_IDEAL = 5.0
COMPLEXITY_WORST = 20.0
LINT_PENALTY_PER_DENSITY = 10.0  # points lost per violation per 100 non-blank lines


def _non_blank_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def lint_score(violations: int, lines: int) -> float:
    density = violations * 100 / max(lines, 1)
    return round(max(0.0, 100.0 - LINT_PENALTY_PER_DENSITY * density), 2)


def complexity_score(average: float) -> float:
    if average <= COMPLEXITY_IDEAL:
        return 100.0
    if average >= COMPLEXITY_WORST:
        return 0.0
    span = COMPLEXITY_WORST - COMPLEXITY_IDEAL
    return round(100.0 - (average - COMPLEXITY_IDEAL) * 100.0 / span, 2)


def lint(files: list[Path], root: Path) -> list[LintResult] | None:
    """Run ruff on the files; None when ruff cannot run."""
    if not files:
        return []
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        "--no-cache",
        "--exit-zero",
        *[str(f) for f in files],
    ]
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=120)
        violations = json.loads(completed.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    counts: dict[Path, int] = {}
    for violation in violations:
        path = Path(violation.get("filename", "")).resolve()
        counts[path] = counts.get(path, 0) + 1
    results = []
    for file in files:
        lines = _non_blank_lines(file.read_text(encoding="utf-8"))
        count = counts.get(file.resolve(), 0)
        results.append(
            LintResult(
                file=_relative(file, root),
                violation_count=count,
                line_count=lines,
                score=lint_score(count, lines),
            )
        )
    return results


def complexity(files: list[Path], root: Path) -> list[ComplexityResult]:
    results = []
    for file in files:
        try:
            blocks = cc_visit(file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        functions = [b for b in blocks if type(b).__name__ == "Function"]
        if not functions:
            results.append(ComplexityResult(file=_relative(file, root), average=0.0, score=100.0))
            continue
        average = sum(b.complexity for b in functions) / len(functions)
        worst = max(functions, key=lambda b: b.complexity)
        results.append(
            ComplexityResult(
                file=_relative(file, root),
                average=round(average, 2),
                worst_name=worst.fullname,
                worst_complexity=worst.complexity,
                score=complexity_score(average),
            )
        )
    return results


def _relative(file: Path, root: Path) -> str:
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.name
