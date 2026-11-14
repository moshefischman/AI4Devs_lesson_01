from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from reviewer.model import Category, Finding, Review, Severity, Usage


def _finding(**overrides):
    data = {
        "file": "src/app.py",
        "line_start": 3,
        "line_end": 4,
        "severity": "medium",
        "category": "bug",
        "title": "Unchecked None",
        "explanation": "value may be None",
    }
    data.update(overrides)
    return Finding(**data)


def test_valid_finding_parses_enums():
    finding = _finding()
    assert finding.severity is Severity.MEDIUM
    assert finding.category is Category.BUG


def test_line_start_below_one_is_rejected():
    with pytest.raises(ValidationError):
        _finding(line_start=0)


def test_line_end_before_line_start_is_rejected():
    with pytest.raises(ValidationError, match="line_end"):
        _finding(line_start=10, line_end=9)


@pytest.mark.parametrize(
    "path", ["/etc/app.py", "C:/repo/app.py", "C:\\repo\\app.py", "../app.py", "a/../../b.py", ""]
)
def test_non_relative_paths_are_rejected(path):
    with pytest.raises(ValidationError):
        _finding(file=path)


def test_backslashes_are_normalized_to_forward_slashes():
    assert _finding(file="src\\app.py").file == "src/app.py"


def test_blank_suggestion_becomes_none():
    assert _finding(suggestion="   ").suggestion is None


def test_severity_order_puts_critical_first():
    assert Severity.CRITICAL.at_least(Severity.HIGH)
    assert not Severity.LOW.at_least(Severity.MEDIUM)
    assert Severity.HIGH.at_least(Severity.HIGH)


def test_usage_addition_sums_every_field():
    total = Usage(input_tokens=1, output_tokens=2, cache_read_input_tokens=3) + Usage(
        input_tokens=10, output_tokens=20, cache_creation_input_tokens=5
    )
    assert total == Usage(
        input_tokens=11, output_tokens=22, cache_read_input_tokens=3, cache_creation_input_tokens=5
    )


def test_review_reports_findings_at_or_above_threshold():
    review = Review(
        findings=[_finding(severity="medium"), _finding(severity="low", line_start=8, line_end=8)],
        summary="",
        model="m",
        usage=Usage(),
        source_count=1,
        created_at=datetime(2026, 11, 14, tzinfo=timezone.utc),
    )
    assert review.has_finding_at_least(Severity.MEDIUM)
    assert not review.has_finding_at_least(Severity.HIGH)
    assert review.count_by_severity()[Severity.LOW] == 1
