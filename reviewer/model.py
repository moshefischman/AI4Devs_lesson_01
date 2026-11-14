"""Domain models for a review. Terms follow `.doc/glossary.md`."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Lower rank means more urgent."""
        return _SEVERITY_ORDER.index(self)

    def at_least(self, other: Severity) -> bool:
        return self.rank <= other.rank


_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"
    TEST = "test"


_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:")


def is_relative_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or _WINDOWS_DRIVE.match(normalized):
        return False
    return ".." not in normalized.split("/")


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    severity: Severity
    category: Category
    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    suggestion: str | None = None

    @field_validator("file")
    @classmethod
    def _file_is_relative(cls, value: str) -> str:
        if not is_relative_path(value):
            raise ValueError("file must be a repository-relative path")
        return value.replace("\\", "/")

    @field_validator("suggestion")
    @classmethod
    def _empty_suggestion_is_none(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None

    @model_validator(mode="after")
    def _line_range_is_ordered(self) -> Finding:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )


class SkippedSource(BaseModel):
    file: str
    reason: str


class Review(BaseModel):
    findings: list[Finding]
    summary: str
    model: str
    usage: Usage
    source_count: int
    skipped: list[SkippedSource] = Field(default_factory=list)
    dropped_finding_count: int = 0
    created_at: datetime

    def count_by_severity(self) -> dict[Severity, int]:
        counts = {severity: 0 for severity in Severity}
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts

    def has_finding_at_least(self, threshold: Severity) -> bool:
        return any(finding.severity.at_least(threshold) for finding in self.findings)


class TestIntent(str, Enum):
    __test__ = False  # not a pytest test class

    HAPPY = "happy"
    EDGE = "edge"
    FAILURE = "failure"


class TestStatus(str, Enum):
    __test__ = False

    PASSED = "passed"
    XFAIL = "xfail"
    DISCARDED = "discarded"


class TestCase(BaseModel):
    __test__ = False

    name: str
    intent: TestIntent
    target: str
    status: TestStatus
    failure_reason: str | None = None


class TestSuite(BaseModel):
    __test__ = False

    target_file: str
    test_file: str
    code: str
    cases: list[TestCase]
    iteration_count: int
    coverage_before: float | None = None
    coverage_after: float | None = None
    bug_findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)

    def count(self, status: TestStatus) -> int:
        return sum(1 for case in self.cases if case.status is status)


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @property
    def rank(self) -> int:
        return [Verdict.PASS, Verdict.WARN, Verdict.FAIL].index(self)


class LintResult(BaseModel):
    file: str
    violation_count: int
    line_count: int
    score: float


class ComplexityResult(BaseModel):
    file: str
    average: float
    worst_name: str | None = None
    worst_complexity: int = 0
    score: float


class FileCoverage(BaseModel):
    file: str
    percent: float


class TestRunSummary(BaseModel):
    __test__ = False

    passed: int = 0
    failed: int = 0
    timed_out: bool = False

    @property
    def total(self) -> int:
        return self.passed + self.failed


class QualityScore(BaseModel):
    """Each component is 0-100, or None when it could not be measured."""

    review: float
    coverage: float | None
    tests: float | None
    static: float | None
    total: float
    weights: dict[str, float]


class QualityReport(BaseModel):
    target: str
    review: Review
    test_suites: list[TestSuite] = Field(default_factory=list)
    coverage: list[FileCoverage] = Field(default_factory=list)
    test_run: TestRunSummary | None = None
    lint: list[LintResult] = Field(default_factory=list)
    complexity: list[ComplexityResult] = Field(default_factory=list)
    score: QualityScore
    verdict: Verdict
    notes: list[str] = Field(default_factory=list)
    created_at: datetime
