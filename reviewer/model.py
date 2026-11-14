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
