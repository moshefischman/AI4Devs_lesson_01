from datetime import datetime, timezone
from pathlib import Path

import pytest

from reviewer.model import (
    ComplexityResult,
    FileCoverage,
    Finding,
    LintResult,
    Review,
    TestRunSummary,
    Usage,
    Verdict,
)
from reviewer.report_service import (
    ReportOptions,
    build_report,
    compute_score,
    review_score,
    verdict_for,
)
from reviewer.source import collect_file
from reviewer.test_runner import RunResult
from tests.factory import FakeGenClient, FakeRunner, gen_suite

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sample_module.py"


def _review(*severities, source_count=1):
    findings = [
        Finding(
            file="a.py",
            line_start=i + 1,
            line_end=i + 1,
            severity=s,
            category="bug",
            title=f"t{i}",
            explanation="e",
        )
        for i, s in enumerate(severities)
    ]
    return Review(
        findings=findings,
        summary="",
        model="fake-model",
        usage=Usage(),
        source_count=source_count,
        created_at=NOW,
    )


def _lint(score):
    return [LintResult(file="a.py", violation_count=0, line_count=10, score=score)]


def _complexity(score):
    return [ComplexityResult(file="a.py", average=1.0, score=score)]


def test_review_score_subtracts_severity_penalties_per_source():
    assert review_score(_review()) == 100.0
    assert review_score(_review("high", "medium")) == 72.0
    assert review_score(_review("high", "high", source_count=4)) == 90.0
    assert review_score(_review(*["critical"] * 3)) == 0.0


def test_total_uses_the_40_30_15_15_weights():
    score = compute_score(
        _review("high"),  # 80
        [FileCoverage(file="a.py", percent=60.0)],  # 60
        TestRunSummary(passed=9, failed=1),  # 90
        _lint(100.0),
        _complexity(80.0),  # static 90
    )
    assert score.total == pytest.approx(0.4 * 80 + 0.3 * 60 + 0.15 * 90 + 0.15 * 90)
    assert score.weights == {"review": 0.4, "coverage": 0.3, "tests": 0.15, "static": 0.15}


def test_missing_components_renormalize_the_weights():
    score = compute_score(_review(), [], None, [], [])
    assert score.total == 100.0
    assert score.coverage is None and score.tests is None and score.static is None
    assert score.weights == {"review": 1.0}


def test_timed_out_tests_score_zero():
    score = compute_score(_review(), [], TestRunSummary(timed_out=True), [], [])
    assert score.tests == 0.0


@pytest.mark.parametrize(
    "severities,coverage,tests,expected",
    [
        ((), 100.0, TestRunSummary(passed=1), Verdict.PASS),  # 100
        # 0.4*80 + 0.3*70 + 0.15*25 + 0.15*100 = 71.75
        (("high",), 70.0, TestRunSummary(passed=1, failed=3), Verdict.WARN),
        # 0.4*60 + 0.3*40 + 0.15*100 + 0.15*100 = 66 -> warn by score...
        (("high", "high"), 40.0, TestRunSummary(passed=1), Verdict.WARN),
        # ...and 0.4*60 + 0.3*0 + 0.15*0 + 0.15*100 = 39 -> fail
        (("high", "high"), 0.0, TestRunSummary(passed=0, failed=1), Verdict.FAIL),
        (("critical",), 100.0, TestRunSummary(passed=1), Verdict.FAIL),  # critical forces fail
    ],
)
def test_verdict_thresholds(severities, coverage, tests, expected):
    result = _review(*severities)
    coverage_list = [FileCoverage(file="a.py", percent=coverage)]
    score = compute_score(result, coverage_list, tests, _lint(100), _complexity(100))
    assert verdict_for(score, result) is expected


def test_critical_finding_forces_fail_even_with_high_score():
    result = _review("critical", source_count=100)
    score = compute_score(result, [], None, [], [])
    assert score.total > 99
    assert verdict_for(score, result) is Verdict.FAIL


def test_build_report_combines_existing_tests_generated_suite_and_metrics():
    sample_key = SAMPLE.resolve().as_posix()
    existing = RunResult(
        outcomes={"test_a": "passed", "test_b": "failed"}, coverage={sample_key: 20.0}
    )
    runner = FakeRunner(coverage=85.0, existing=existing)
    code = "def test_clamp_happy():\n    pass\n\ndef test_median_even():\n    assert False\n"
    bug = {
        "test_name": "test_median_even",
        "severity": "high",
        "line_start": 27,
        "line_end": 27,
        "title": "Median wrong for even-length lists",
        "explanation": "upper middle element",
    }
    runner.behavior = {"test_median_even": "failed"}
    client = FakeGenClient([gen_suite(code, bugs=[bug])])

    report = build_report(
        [collect_file(SAMPLE, root=ROOT)],
        ROOT,
        client,
        runner,
        ReportOptions(with_test_gen=True, tests_path=ROOT / "tests"),
        target="tests/fixtures/sample_module.py",
        now=NOW,
    )

    assert report.test_run == TestRunSummary(passed=1 + 2, failed=1)
    assert report.coverage == [FileCoverage(file="tests/fixtures/sample_module.py", percent=85.0)]
    assert report.test_suites[0].coverage_before == 20.0
    assert any(f.title.startswith("Median wrong") for f in report.review.findings)
    assert report.lint and report.complexity
    assert report.created_at == NOW


def test_build_report_without_tests_adds_notes():
    report = build_report(
        [collect_file(SAMPLE, root=ROOT)],
        ROOT,
        FakeGenClient([gen_suite("")]),
        FakeRunner(),
        now=NOW,
    )
    assert report.test_run is None
    assert any("no existing test directory" in note for note in report.notes)
    assert any("coverage was not measured" in note for note in report.notes)
    assert report.test_suites == []


def test_build_report_for_non_python_sources_skips_python_metrics(tmp_path):
    (tmp_path / "app.js").write_text("export const a = 1\n", encoding="utf-8")
    report = build_report(
        [collect_file(tmp_path / "app.js", root=tmp_path)],
        tmp_path,
        FakeGenClient([gen_suite("")]),
        FakeRunner(),
    )
    assert report.lint == [] and report.complexity == []
    assert any("no Python files" in note for note in report.notes)
