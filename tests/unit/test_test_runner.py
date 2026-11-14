"""These tests start real pytest subprocesses (about a second each)."""

from pathlib import Path

import pytest

from reviewer.test_runner import SubprocessRunner, is_secret_var, parse_junit

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sample_module.py"

SUITE = """
import os
import socket

import pytest

from tests.fixtures.sample_module import clamp, median


def test_clamp_passes():
    assert clamp(5, 0, 3) == 3


def test_clamp_fails():
    assert clamp(5, 0, 3) == 5


@pytest.mark.xfail(strict=True, reason="seeded bug")
def test_median_even_length():
    assert median([1, 2, 3, 4]) == 2.5


@pytest.mark.parametrize("value", [1, 99])
def test_param_one_bad(value):
    assert clamp(value, 0, 10) == value


def test_network_is_blocked():
    socket.create_connection(("example.com", 80), timeout=1)


def test_no_credentials_in_env():
    leaked = [k for k in os.environ if k.startswith("ANTHROPIC_") or "TOKEN" in k.upper()]
    assert leaked == []


class TestGroup:
    def test_method(self):
        assert clamp(1, 0, 2) == 1
"""


@pytest.fixture(scope="module")
def sample_run():
    mp = pytest.MonkeyPatch()
    mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    mp.setenv("SOME_SERVICE_TOKEN", "not-real")
    try:
        yield SubprocessRunner(ROOT).run_generated(SUITE, "test_sample_module.py", [SAMPLE])
    finally:
        mp.undo()


def test_outcomes_are_reported_per_test_function(sample_run):
    assert sample_run.ok
    assert sample_run.outcomes["test_clamp_passes"] == "passed"
    assert sample_run.outcomes["test_clamp_fails"] == "failed"
    assert sample_run.outcomes["test_median_even_length"] == "xfailed"
    assert sample_run.outcomes["TestGroup.test_method"] == "passed"


def test_parametrized_test_takes_its_worst_outcome(sample_run):
    assert sample_run.outcomes["test_param_one_bad"] == "failed"


def test_failure_messages_are_captured(sample_run):
    assert "assert 3 == 5" in sample_run.messages["test_clamp_fails"]


def test_network_access_is_disabled(sample_run):
    assert sample_run.outcomes["test_network_is_blocked"] == "failed"
    assert "network access is disabled" in sample_run.messages["test_network_is_blocked"]


def test_credential_variables_are_not_passed_to_the_subprocess(sample_run):
    assert sample_run.outcomes["test_no_credentials_in_env"] == "passed"


def test_coverage_of_the_target_is_measured(sample_run):
    percent = sample_run.coverage[SAMPLE.resolve().as_posix()]
    assert 0 < percent < 100


def test_hanging_suite_is_stopped_by_timeout():
    code = "import time\n\ndef test_hang():\n    time.sleep(30)\n"
    result = SubprocessRunner(ROOT, timeout=2).run_generated(code, "test_x.py", [SAMPLE])
    assert result.timed_out
    assert not result.ok


def test_syntax_error_is_a_collection_error():
    result = SubprocessRunner(ROOT).run_generated("def broken(:\n", "test_x.py", [SAMPLE])
    assert result.collection_error
    assert result.outcomes == {}


def test_reverse_order_runs_tests_backwards():
    code = (
        "ORDER = []\n\n"
        "def test_a():\n    ORDER.append('a')\n\n"
        "def test_b():\n    assert ORDER == ['a']\n"
    )
    runner = SubprocessRunner(ROOT)
    assert runner.run_generated(code, "test_o.py", [SAMPLE]).outcomes["test_b"] == "passed"
    reversed_run = runner.run_generated(code, "test_o.py", [SAMPLE], reverse=True)
    assert reversed_run.outcomes["test_b"] == "failed"


def test_run_existing_uses_the_project_tests(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    result = SubprocessRunner(tmp_path).run_existing(
        [tmp_path / "test_calc.py"], [tmp_path / "calc.py"]
    )
    assert result.outcomes == {"test_add": "passed"}
    assert result.coverage[(tmp_path / "calc.py").resolve().as_posix()] == 100.0


@pytest.mark.parametrize(
    "name,secret",
    [
        ("ANTHROPIC_API_KEY", True),
        ("ANTHROPIC_BASE_URL", True),
        ("LINEAR_API_KEY", True),
        ("GITHUB_TOKEN", True),
        ("DB_PASSWORD", True),
        ("PATH", False),
        ("SYSTEMROOT", False),
    ],
)
def test_is_secret_var(name, secret):
    assert is_secret_var(name) is secret


def test_parse_junit_reads_xpass_and_collection_errors(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="test_x" name="test_strict">'
        '<failure message="[XPASS(strict)] bug"/></testcase>'
        '<testcase classname="" name="test_x">'
        '<error message="collection failure">ImportError</error></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    outcomes, _messages, collection_error = parse_junit(xml)
    assert outcomes == {"test_strict": "xpassed"}
    assert "ImportError" in collection_error
