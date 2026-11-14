import subprocess

import pytest

from reviewer import metric


@pytest.mark.parametrize(
    "violations,lines,expected", [(0, 50, 100.0), (1, 100, 90.0), (20, 100, 0.0)]
)
def test_lint_score_scales_with_violation_density(violations, lines, expected):
    assert metric.lint_score(violations, lines) == expected


@pytest.mark.parametrize("average,expected", [(1, 100.0), (5, 100.0), (12.5, 50.0), (25, 0.0)])
def test_complexity_score_is_linear_between_5_and_20(average, expected):
    assert metric.complexity_score(average) == expected


def test_complexity_reports_average_and_worst_function(tmp_path):
    source = tmp_path / "m.py"
    source.write_text(
        "def simple():\n    return 1\n\n"
        "def branchy(x):\n    if x > 1:\n        return 1\n"
        "    elif x < 0:\n        return 2\n    return 3\n",
        encoding="utf-8",
    )
    [result] = metric.complexity([source], tmp_path)
    assert result.file == "m.py"
    assert result.worst_name == "branchy"
    assert result.worst_complexity == 3
    assert result.average == 2.0
    assert result.score == 100.0


def test_complexity_of_file_without_functions_is_perfect(tmp_path):
    (tmp_path / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert metric.complexity([tmp_path / "c.py"], tmp_path)[0].score == 100.0


def test_lint_counts_ruff_violations(tmp_path):
    source = tmp_path / "m.py"
    source.write_text("import os\n\nVALUE = 1\n", encoding="utf-8")
    [result] = metric.lint([source], tmp_path)
    assert result.violation_count >= 1
    assert result.line_count == 2
    assert result.score < 100


def test_lint_returns_none_when_ruff_cannot_run(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    def broken(*args, **kwargs):
        raise OSError("ruff missing")

    monkeypatch.setattr(subprocess, "run", broken)
    assert metric.lint([tmp_path / "m.py"], tmp_path) is None


def test_lint_of_no_files_is_empty(tmp_path):
    assert metric.lint([], tmp_path) == []
