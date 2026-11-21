"""Quality report: review + tests + coverage + static metrics → score → verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import metric
from .error import InputError
from .model import (
    ComplexityResult,
    FileCoverage,
    LintResult,
    QualityReport,
    QualityScore,
    Review,
    Severity,
    TestRunSummary,
    TestStatus,
    TestSuite,
    Verdict,
)
from .review_service import ReviewOptions, deduplicate, review, sort_findings
from .source import Source
from .test_gen_service import DEFAULT_MAX_ITERATION, GenerationClient, generate
from .test_runner import TestRunner
from .test_target import analyze

WEIGHTS = {"review": 0.40, "coverage": 0.30, "tests": 0.15, "static": 0.15}
SEVERITY_PENALTY = {
    Severity.CRITICAL: 40.0,
    Severity.HIGH: 20.0,
    Severity.MEDIUM: 8.0,
    Severity.LOW: 3.0,
    Severity.INFO: 0.0,
}
PASS_THRESHOLD = 80.0
WARN_THRESHOLD = 60.0


@dataclass
class ReportOptions:
    review: ReviewOptions = field(default_factory=ReviewOptions)
    with_test_gen: bool = False
    tests_path: Path | None = None
    max_iteration: int = DEFAULT_MAX_ITERATION
    max_test_gen_files: int = 10


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def review_score(result: Review) -> float:
    penalty = sum(SEVERITY_PENALTY[f.severity] for f in result.findings)
    return round(max(0.0, 100.0 - penalty / max(result.source_count, 1)), 2)


def compute_score(
    result: Review,
    coverage: list[FileCoverage],
    test_run: TestRunSummary | None,
    lint: list[LintResult],
    complexity: list[ComplexityResult],
) -> QualityScore:
    tests = None
    if test_run is not None:
        if test_run.timed_out:
            tests = 0.0
        elif test_run.total:
            tests = round(test_run.passed * 100 / test_run.total, 2)
    static_parts = [
        part
        for part in (_mean([r.score for r in lint]), _mean([r.score for r in complexity]))
        if part is not None
    ]
    components = {
        "review": review_score(result),
        "coverage": _mean([c.percent for c in coverage]),
        "tests": tests,
        "static": _mean(static_parts),
    }
    available = {k: v for k, v in components.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in available)
    total = sum(WEIGHTS[k] * v for k, v in available.items()) / weight_sum
    return QualityScore(
        review=components["review"],
        coverage=components["coverage"],
        tests=components["tests"],
        static=components["static"],
        total=round(total, 2),
        weights={k: round(WEIGHTS[k] / weight_sum, 4) for k in available},
    )


def verdict_for(score: QualityScore, result: Review) -> Verdict:
    if result.has_finding_at_least(Severity.CRITICAL) or score.total < WARN_THRESHOLD:
        return Verdict.FAIL
    return Verdict.PASS if score.total >= PASS_THRESHOLD else Verdict.WARN


def build_report(
    sources: list[Source],
    root: Path,
    client: GenerationClient,
    runner: TestRunner,
    options: ReportOptions | None = None,
    target: str = ".",
    now: datetime | None = None,
) -> QualityReport:
    options = options or ReportOptions()
    notes: list[str] = []
    result = review(sources, client, options.review, now=now)  # type: ignore[arg-type]

    python_files = [root / s.path for s in sources if s.path.endswith(".py")]
    python_files = list(dict.fromkeys(python_files))
    keys = {f.resolve().as_posix(): f for f in python_files}

    coverage: dict[str, float] = {}
    test_run: TestRunSummary | None = None
    if not python_files:
        notes.append("no Python files: coverage, tests, and static metrics were not measured")
    elif options.tests_path and options.tests_path.exists():
        run = runner.run_existing([options.tests_path], python_files)
        if run.timed_out:
            test_run = TestRunSummary(timed_out=True)
            notes.append(f"existing tests in {options.tests_path.as_posix()} timed out")
        elif run.collection_error:
            notes.append(f"existing tests could not be collected: {run.collection_error[:200]}")
        else:
            passed = len(run.with_outcome("passed", "xfailed"))  # skipped tests do not count
            test_run = TestRunSummary(
                passed=passed, failed=len(run.with_outcome("failed", "xpassed"))
            )
            coverage.update(run.coverage)
    else:
        notes.append("no existing test directory found: run with --tests PATH to measure it")

    suites: list[TestSuite] = []
    if options.with_test_gen:
        candidates = python_files[: options.max_test_gen_files]
        if len(python_files) > len(candidates):
            notes.append(
                f"test generation limited to the first {len(candidates)} of "
                f"{len(python_files)} Python files (--max-test-gen-files)"
            )
        for file in candidates:
            try:
                test_target = analyze(file, root)
            except InputError as exc:
                notes.append(f"test-gen skipped: {exc}")
                continue
            key = file.resolve().as_posix()
            suite = generate(
                test_target,
                root,
                client,
                runner,
                max_iteration=options.max_iteration,
                coverage_before=coverage.get(key, 0.0),
            )
            suites.append(suite)
            if suite.coverage_after is not None:
                coverage[key] = max(coverage.get(key, 0.0), suite.coverage_after)
            kept = suite.count(TestStatus.PASSED) + suite.count(TestStatus.XFAIL)
            test_run = test_run or TestRunSummary()
            test_run = TestRunSummary(
                passed=test_run.passed + kept, failed=test_run.failed, timed_out=test_run.timed_out
            )

    bug_findings = [f for suite in suites for f in suite.bug_findings]
    if bug_findings:
        merged = sort_findings(deduplicate([*result.findings, *bug_findings]))
        result = result.model_copy(update={"findings": merged})

    file_coverage = [
        FileCoverage(file=_relative(keys[key], root), percent=pct)
        for key, pct in coverage.items()
        if key in keys
    ]
    lint = metric.lint(python_files, root)
    if lint is None:
        notes.append("ruff could not run: lint was not measured")
        lint = []
    complexity = metric.complexity(python_files, root)

    score = compute_score(result, file_coverage, test_run, lint, complexity)
    for name in WEIGHTS:
        if name not in score.weights:
            notes.append(f"{name} was not measured; weights were renormalized")
    return QualityReport(
        target=target,
        review=result,
        test_suites=suites,
        coverage=sorted(file_coverage, key=lambda c: c.file),
        test_run=test_run,
        lint=lint,
        complexity=complexity,
        score=score,
        verdict=verdict_for(score, result),
        notes=notes,
        created_at=now or datetime.now(timezone.utc),
    )


def _relative(file: Path, root: Path) -> str:
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.name
