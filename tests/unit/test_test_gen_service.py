from pathlib import Path

import pytest

from reviewer.error import ApiError
from reviewer.model import Category, TestStatus
from reviewer.test_gen_service import generate, mark_xfail, remove_tests, with_header
from reviewer.test_runner import GENERATED_HEADER
from reviewer.test_target import analyze
from tests.factory import FakeGenClient, FakeRunner, gen_suite

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sample_module.py"

TWO_TESTS = (
    "from tests.fixtures.sample_module import clamp\n\n"
    "def test_clamp_happy():\n    assert clamp(1, 0, 2) == 1\n\n"
    "def test_clamp_failure():\n    assert clamp(1, 0, 2) == 1\n"
)
MEDIAN_BUG = {
    "test_name": "test_median_even",
    "severity": "high",
    "line_start": 27,
    "line_end": 27,
    "title": "Median wrong for even-length lists",
    "explanation": "Returns the upper middle element instead of the mean of both.",
}


@pytest.fixture(scope="module")
def target():
    return analyze(SAMPLE, ROOT)


def _generate(target, suites, runner, **kwargs):
    client = FakeGenClient(suites)
    return generate(target, ROOT, client, runner, **kwargs), client


def test_passing_suite_is_kept_after_one_iteration(target):
    suite, client = _generate(target, [gen_suite(TWO_TESTS)], FakeRunner(coverage=64.0))

    assert suite.iteration_count == 1
    assert [c.status for c in suite.cases] == [TestStatus.PASSED, TestStatus.PASSED]
    assert suite.code.startswith(GENERATED_HEADER)
    assert suite.coverage_after == 64.0
    assert suite.test_file == "test_sample_module.py"
    assert 'module="tests.fixtures.sample_module"' in client.prompts[0]


def test_failing_test_triggers_a_repair_round(target):
    broken = TWO_TESTS.replace("test_clamp_failure", "test_wrong")
    runner = FakeRunner({"test_wrong": "failed"})

    suite, client = _generate(target, [gen_suite(broken), gen_suite(TWO_TESTS)], runner)

    assert suite.iteration_count == 2
    assert "<run_result>" in client.prompts[1]
    assert "AssertionError in test_wrong" in client.prompts[1]
    assert all(c.status is TestStatus.PASSED for c in suite.cases)


def test_loop_stops_at_max_iteration_and_discards_failing_tests(target):
    runner = FakeRunner({"test_clamp_failure": "failed"})

    suite, client = _generate(target, [gen_suite(TWO_TESTS)], runner, max_iteration=3)

    assert len(client.prompts) == 3
    assert suite.iteration_count == 3
    statuses = {c.name: c.status for c in suite.cases}
    assert statuses == {
        "test_clamp_happy": TestStatus.PASSED,
        "test_clamp_failure": TestStatus.DISCARDED,
    }
    assert "def test_clamp_failure" not in suite.code
    assert (
        "AssertionError"
        in next(c for c in suite.cases if c.name == "test_clamp_failure").failure_reason
    )


def test_suspected_bug_is_kept_as_strict_xfail_with_a_finding(target):
    code = TWO_TESTS + "\n\ndef test_median_even():\n    assert False\n"
    suite_in = gen_suite(code, bugs=[MEDIAN_BUG])
    runner = FakeRunner({"test_median_even": "failed"})

    suite, _ = _generate(target, [suite_in], runner)

    case = next(c for c in suite.cases if c.name == "test_median_even")
    assert case.status is TestStatus.XFAIL
    assert "strict=True" in suite.code
    assert "import pytest" in suite.code
    finding = suite.bug_findings[0]
    assert finding.category is Category.BUG
    assert (finding.file, finding.line_start) == ("tests/fixtures/sample_module.py", 27)


def test_suspected_bug_with_invalid_lines_gets_no_finding(target):
    code = TWO_TESTS + "\n\ndef test_median_even():\n    assert False\n"
    bad_bug = {**MEDIAN_BUG, "line_start": 500, "line_end": 501}
    suite, _ = _generate(
        target, [gen_suite(code, bugs=[bad_bug])], FakeRunner({"test_median_even": "failed"})
    )
    assert suite.bug_findings == []


def test_order_dependent_test_is_discarded(target):
    runner = FakeRunner({"test_clamp_failure": lambda reverse: "failed" if reverse else "passed"})

    suite, _ = _generate(target, [gen_suite(TWO_TESTS)], runner)

    case = next(c for c in suite.cases if c.name == "test_clamp_failure")
    assert case.status is TestStatus.DISCARDED
    assert "order-dependent" in case.failure_reason
    assert any(reverse for _code, reverse in runner.runs)


def test_suite_that_never_compiles_is_discarded_entirely(target):
    suite, client = _generate(target, [gen_suite("def test_x(:\n", cases=[])], FakeRunner())

    assert len(client.prompts) == 3
    assert suite.code == ""
    assert "no runnable suite" in suite.warnings[0]
    assert "does not compile" in client.prompts[1]


def test_persistent_collection_error_discards_every_case(target):
    runner = FakeRunner({"test_clamp_happy": "collection_error"})
    suite, _ = _generate(target, [gen_suite(TWO_TESTS)], runner, max_iteration=2)
    assert suite.code == ""
    assert all(c.status is TestStatus.DISCARDED for c in suite.cases)


def test_missing_happy_and_failure_tests_are_warned(target):
    suite, _ = _generate(
        target, [gen_suite(TWO_TESTS, cases=[("test_clamp_happy", "happy", "clamp")])], FakeRunner()
    )
    assert "clamp: no failure test kept" in suite.warnings
    assert any(w.startswith("median: no happy / failure") for w in suite.warnings)
    assert "test test_clamp_failure was not declared in cases" in suite.warnings


def test_api_error_propagates(target):
    with pytest.raises(ApiError):
        _generate(target, [ApiError("boom")], FakeRunner())


def test_remove_tests_drops_functions_with_decorators_and_empty_classes():
    code = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('v', [1])\n"
        "def test_a(v):\n    pass\n\n"
        "def test_b():\n    pass\n\n"
        'class TestC:\n    """Doc."""\n\n    def test_c(self):\n        pass\n'
    )
    result = remove_tests(code, {"test_a", "TestC.test_c"})
    assert "test_a" not in result
    assert "parametrize" not in result
    assert "class TestC" not in result
    assert "def test_b" in result
    compile(result, "x", "exec")


def test_mark_xfail_adds_import_after_future_imports():
    code = "from __future__ import annotations\n\ndef test_a():\n    assert False\n"
    result = mark_xfail(code, {"test_a": "it's broken"})
    lines = result.splitlines()
    assert lines[1] == "import pytest"
    assert '@pytest.mark.xfail(reason="posible bug: it\'s broken", strict=True)' in result
    compile(result, "x", "exec")


def test_with_header_is_idempotent():
    once = with_header("x = 1\n")
    assert with_header(once) == once
