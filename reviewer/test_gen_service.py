"""Generate a pytest suite for one module: generate → run → repair → stabilize."""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .claude_client import StructuredResponse
from .model import (
    Category,
    Finding,
    Severity,
    TestCase,
    TestIntent,
    TestStatus,
    TestSuite,
    Usage,
)
from .test_runner import GENERATED_HEADER, RunResult, TestRunner
from .test_target import TestTarget

log = logging.getLogger(__name__)

DEFAULT_MAX_ITERATION = 3
STABILIZE_ROUNDS = 2


class GeneratedCase(BaseModel):
    name: str
    intent: TestIntent
    target: str


class SuspectedBug(BaseModel):
    test_name: str
    severity: Severity
    line_start: int
    line_end: int
    title: str
    explanation: str


class GeneratedSuite(BaseModel):
    code: str
    cases: list[GeneratedCase]
    suspected_bugs: list[SuspectedBug]


GENERATED_SUITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "intent": {"type": "string", "enum": [i.value for i in TestIntent]},
                    "target": {"type": "string"},
                },
                "required": ["name", "intent", "target"],
                "additionalProperties": False,
            },
        },
        "suspected_bugs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test_name": {"type": "string"},
                    "severity": {"type": "string", "enum": [s.value for s in Severity]},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "test_name",
                    "severity",
                    "line_start",
                    "line_end",
                    "title",
                    "explanation",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["code", "cases", "suspected_bugs"],
    "additionalProperties": False,
}


class GenerationClient(Protocol):
    model: str

    def complete(
        self, system: str, content: str, schema: dict[str, Any], result_type: type
    ) -> StructuredResponse: ...


def load_system_prompt() -> str:
    return resources.files("reviewer.prompt").joinpath("test_gen_system.md").read_text("utf-8")


# ---------------------------------------------------------------- prompt content


def _numbered(text: str) -> str:
    return "\n".join(f"{n:>5} | {line}" for n, line in enumerate(text.splitlines(), start=1))


def target_content(target: TestTarget) -> str:
    parts = [
        f'<target module="{target.module}" path="{target.path}">',
        _numbered(target.source_text),
        "</target>",
        "",
        "Public functions and methods:",
        *(f"- {c.qualified_name}: `{c.signature}`" for c in target.callables),
        "",
        "External dependencies to fake or freeze: " + (", ".join(target.external) or "none"),
        f"Import the code under test with `from {target.module} import ...` "
        f"or `import {target.module} as target`.",
    ]
    for path, text in target.existing_tests.items():
        parts += ["", f'<existing_test path="{path}">', text, "</existing_test>"]
    return "\n".join(parts)


def describe_failures(result: RunResult | None, syntax_error: str | None) -> str:
    if syntax_error:
        return f"The file does not compile: {syntax_error}"
    assert result is not None
    if result.timed_out:
        return "The suite timed out. Remove sleeps, loops, or blocking calls."
    if result.collection_error:
        return f"pytest could not collect the file:\n{result.collection_error}"
    lines = []
    for name in result.with_outcome("failed", "xpassed"):
        lines.append(f"--- {name}\n{result.messages.get(name, '(no message)')}")
    return "\n\n".join(lines)


def repair_content(target: TestTarget, suite: GeneratedSuite, failures: str) -> str:
    return "\n".join(
        [
            target_content(target),
            "",
            "<previous_suite>",
            suite.code,
            "</previous_suite>",
            "",
            "<run_result>",
            failures,
            "</run_result>",
            "",
            "Return the complete corrected file.",
        ]
    )


# ---------------------------------------------------------------- code transforms


def _short(name: str) -> str:
    return name.split(".")[-1]


def _test_nodes(tree: ast.Module) -> list[tuple[str, ast.AST, ast.ClassDef | None]]:
    found: list[tuple[str, ast.AST, ast.ClassDef | None]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.name, node, None))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((f"{node.name}.{item.name}", item, node))
    return found


def _first_line(node: Any) -> int:
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


def remove_tests(code: str, names: set[str]) -> str:
    """Delete the named test functions (and classes left without tests)."""
    if not names:
        return code
    tree = ast.parse(code)
    spans: list[tuple[int, int]] = []
    removed_by_class: dict[ast.ClassDef, int] = {}
    for key, node, owner in _test_nodes(tree):
        if key in names or (owner is None and _short(key) in names):
            spans.append((_first_line(node), node.end_lineno or node.lineno))
            if owner is not None:
                removed_by_class[owner] = removed_by_class.get(owner, 0) + 1
    for owner, count in removed_by_class.items():
        body = [n for n in owner.body if not (isinstance(n, ast.Expr) and n is owner.body[0])]
        if count >= len(body):
            spans = [s for s in spans if not (owner.lineno <= s[0] <= (owner.end_lineno or 0))]
            spans.append((_first_line(owner), owner.end_lineno or owner.lineno))
    lines = code.splitlines()
    for start, end in sorted(spans, reverse=True):
        del lines[start - 1 : end]
    return "\n".join(lines) + "\n"


def _ensure_pytest_import(lines: list[str], tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == "pytest" for a in node.names):
            return lines
    insert_at = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            insert_at = node.end_lineno or node.lineno
    if insert_at == 0 and lines and lines[0].startswith("#"):
        insert_at = 1
    return [*lines[:insert_at], "import pytest", *lines[insert_at:]]


def mark_xfail(code: str, reasons: dict[str, str]) -> str:
    """Add @pytest.mark.xfail(strict=True) above each named test."""
    if not reasons:
        return code
    tree = ast.parse(code)
    lines = code.splitlines()
    inserts: list[tuple[int, str]] = []
    for key, node, _owner in _test_nodes(tree):
        reason = reasons.get(key) or reasons.get(_short(key))
        if reason is None:
            continue
        indent = " " * node.col_offset  # type: ignore[attr-defined]
        decorator = (
            f"{indent}@pytest.mark.xfail(reason={('posible bug: ' + reason)!r}, strict=True)"
        )
        inserts.append((_first_line(node), decorator))
    for line_no, decorator in sorted(inserts, reverse=True):
        lines.insert(line_no - 1, decorator)
    return "\n".join(_ensure_pytest_import(lines, ast.parse("\n".join(lines)))) + "\n"


def with_header(code: str) -> str:
    return code if code.startswith(GENERATED_HEADER) else f"{GENERATED_HEADER}\n{code}"


def _syntax_error(code: str) -> str | None:
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return f"{exc.msg} (line {exc.lineno})"
    return None


# ---------------------------------------------------------------- service


@dataclass
class _State:
    suite: GeneratedSuite
    usage: Usage = field(default_factory=Usage)
    iterations: int = 0
    result: RunResult | None = None
    syntax_error: str | None = None


def _matches(case_name: str, key: str) -> bool:
    return case_name == key or _short(case_name) == _short(key)


def _ask(client: GenerationClient, system: str, content: str, state: _State | None) -> _State:
    response = client.complete(system, content, GENERATED_SUITE_SCHEMA, GeneratedSuite)
    usage = response.usage if state is None else state.usage + response.usage
    iterations = 1 if state is None else state.iterations + 1
    return _State(suite=response.result, usage=usage, iterations=iterations)


def _run(state: _State, runner: TestRunner, file_name: str, target_path: Path) -> None:
    state.suite.code = with_header(state.suite.code)
    state.syntax_error = _syntax_error(state.suite.code)
    state.result = (
        None
        if state.syntax_error
        else runner.run_generated(state.suite.code, file_name, [target_path])
    )


def _unexplained_failures(state: _State) -> list[str]:
    if state.syntax_error or state.result is None or not state.result.ok:
        return ["<suite>"]
    bugs = {b.test_name for b in state.suite.suspected_bugs}
    return [
        name
        for name in state.result.with_outcome("failed", "xpassed")
        if not any(_matches(bug, name) for bug in bugs)
    ]


def stabilize(
    code: str, runner: TestRunner, file_name: str, target_path: Path
) -> tuple[str, RunResult | None, dict[str, str]]:
    """Run normal + reversed order; drop failing or order-dependent tests until stable."""
    dropped: dict[str, str] = {}
    result: RunResult | None = None
    for _ in range(STABILIZE_ROUNDS + 1):
        result = runner.run_generated(code, file_name, [target_path])
        reverse = runner.run_generated(code, file_name, [target_path], reverse=True)
        if not result.ok or not reverse.ok:
            return code, None, dropped
        bad: dict[str, str] = {}
        for name, outcome in result.outcomes.items():
            if outcome not in ("passed", "xfailed"):
                bad[name] = result.messages.get(name, outcome)
            elif reverse.outcomes.get(name) != outcome:
                bad[name] = "order-dependent: outcome changed when run in reverse order"
        if not bad:
            return code, result, dropped
        dropped.update(bad)
        code = remove_tests(code, set(bad))
        if not _test_nodes(ast.parse(code)):
            return code, None, dropped
    return code, result, dropped


def _bug_finding(bug: SuspectedBug, target: TestTarget, test_name: str) -> Finding | None:
    line_count = len(target.source_text.splitlines())
    if not (1 <= bug.line_start <= bug.line_end <= line_count):
        return None
    return Finding(
        file=target.path,
        line_start=bug.line_start,
        line_end=bug.line_end,
        severity=bug.severity,
        category=Category.BUG,
        title=bug.title,
        explanation=f"{bug.explanation} (exposed by generated test `{test_name}`)",
    )


def _missing_intent_warnings(target: TestTarget, cases: list[TestCase]) -> list[str]:
    warnings = []
    kept = [c for c in cases if c.status is not TestStatus.DISCARDED]
    for callable_ in target.callables:
        names = {callable_.qualified_name, callable_.name}
        intents = {c.intent for c in kept if c.target in names}
        missing = [i.value for i in (TestIntent.HAPPY, TestIntent.FAILURE) if i not in intents]
        if missing:
            warnings.append(f"{callable_.qualified_name}: no {' / '.join(missing)} test kept")
    return warnings


def generate(
    target: TestTarget,
    root: Path,
    client: GenerationClient,
    runner: TestRunner,
    max_iteration: int = DEFAULT_MAX_ITERATION,
    coverage_before: float | None = None,
) -> TestSuite:
    system = load_system_prompt()
    target_path = root / target.path
    file_name = target.test_file_name
    target_key = target_path.resolve().as_posix()

    state = _ask(client, system, target_content(target), None)
    _run(state, runner, file_name, target_path)
    while _unexplained_failures(state) and state.iterations < max_iteration:
        failures = describe_failures(state.result, state.syntax_error)
        log.debug("iteration %d failed; asking for a repair", state.iterations)
        repaired = _ask(client, system, repair_content(target, state.suite, failures), state)
        _run(repaired, runner, file_name, target_path)
        state = repaired

    warnings: list[str] = []
    suite = state.suite
    if state.syntax_error or state.result is None or not state.result.ok:
        reason = describe_failures(state.result, state.syntax_error)
        warnings.append(f"no runnable suite after {state.iterations} iteration(s)")
        cases = [
            TestCase(
                name=c.name,
                intent=c.intent,
                target=c.target,
                status=TestStatus.DISCARDED,
                failure_reason=reason[:300],
            )
            for c in suite.cases
        ]
        return TestSuite(
            target_file=target.path,
            test_file=file_name,
            code="",
            cases=cases,
            iteration_count=state.iterations,
            coverage_before=coverage_before,
            warnings=warnings + _missing_intent_warnings(target, cases),
            usage=state.usage,
        )

    failing = state.result.with_outcome("failed", "xpassed")
    bugs = {
        name: bug
        for name in failing
        for bug in suite.suspected_bugs
        if _matches(bug.test_name, name)
    }
    discard = {
        name: state.result.messages.get(name, "failed") for name in failing if name not in bugs
    }
    code = remove_tests(suite.code, set(discard))
    code = mark_xfail(code, {name: bug.title for name, bug in bugs.items()})

    code, final, dropped = stabilize(code, runner, file_name, target_path)
    discard.update(dropped)
    if final is None:
        warnings.append("no generated test survived stabilization")
        code = ""

    outcomes = final.outcomes if final else {}
    cases: list[TestCase] = []
    for case in suite.cases:
        key = next((k for k in outcomes if _matches(case.name, k)), None)
        outcome = outcomes.get(key) if key else None
        if outcome == "passed":
            status, reason = TestStatus.PASSED, None
        elif outcome == "xfailed":
            status, reason = TestStatus.XFAIL, "expected failure: suspected bug in the target"
        else:
            status = TestStatus.DISCARDED
            reason = next(
                (msg for name, msg in discard.items() if _matches(case.name, name)),
                "not present in the final suite",
            )[:300]
        cases.append(
            TestCase(
                name=case.name,
                intent=case.intent,
                target=case.target,
                status=status,
                failure_reason=reason,
            )
        )
    declared = {c.name for c in suite.cases}
    for key in outcomes:
        if not any(_matches(name, key) for name in declared):
            warnings.append(f"test {key} was not declared in cases")

    findings = []
    for case in cases:
        if case.status is TestStatus.XFAIL:
            bug = next((b for n, b in bugs.items() if _matches(case.name, n)), None)
            finding = _bug_finding(bug, target, case.name) if bug else None
            if finding:
                findings.append(finding)

    return TestSuite(
        target_file=target.path,
        test_file=file_name,
        code=code,
        cases=cases,
        iteration_count=state.iterations,
        coverage_before=coverage_before,
        coverage_after=final.coverage.get(target_key) if final else None,
        bug_findings=findings,
        warnings=warnings + _missing_intent_warnings(target, cases),
        usage=state.usage,
    )
