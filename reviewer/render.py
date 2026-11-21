"""Render reviews, test suites, and quality reports as text, JSON, or Markdown."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .model import Finding, QualityReport, Review, Severity, TestStatus, TestSuite

FORMATS = ("text", "json", "markdown")

_COLOR = {
    Severity.CRITICAL: "\033[1;31m",
    Severity.HIGH: "\033[31m",
    Severity.MEDIUM: "\033[33m",
    Severity.LOW: "\033[36m",
    Severity.INFO: "\033[2m",
}
_RESET = "\033[0m"


def _location(finding: Finding) -> str:
    if finding.line_start == finding.line_end:
        return f"{finding.file}:{finding.line_start}"
    return f"{finding.file}:{finding.line_start}-{finding.line_end}"


def to_text(review: Review, color: bool = False) -> str:
    out: list[str] = []
    for finding in review.findings:
        label = f"[{finding.severity.value.upper()}]"
        if color:
            label = f"{_COLOR[finding.severity]}{label}{_RESET}"
        out.append(f"{label} {_location(finding)} ({finding.category.value}) {finding.title}")
        out.append(f"    {finding.explanation}")
        if finding.suggestion:
            out.extend(f"    > {line}" for line in finding.suggestion.splitlines())
        out.append("")
    for skipped in review.skipped:
        out.append(f"[SKIPPED] {skipped.file}: {skipped.reason}")
    if review.skipped:
        out.append("")
    out.append(review.summary)
    return "\n".join(out)


def to_json(review: Review) -> str:
    return review.model_dump_json(indent=2)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def to_markdown(review: Review) -> str:
    out = [
        "# Code Review",
        "",
        f"- Model: `{review.model}`",
        f"- Sources: {review.source_count}",
        f"- Created: {review.created_at.isoformat()}",
        "",
        "## Summary",
        "",
        review.summary,
        "",
        "## Findings",
        "",
    ]
    if not review.findings:
        out.append("No findings.")
    else:
        out.append("| Severity | Category | Location | Title |")
        out.append("|---|---|---|---|")
        for f in review.findings:
            out.append(
                f"| {f.severity.value} | {f.category.value} | `{_location(f)}` | {_cell(f.title)} |"
            )
        out.append("")
        out.append("## Details")
        for f in review.findings:
            out += ["", f"### `{_location(f)}` — {f.title}", "", f.explanation]
            if f.suggestion:
                out += ["", "```", f.suggestion, "```"]
    if review.skipped:
        out += ["", "## Skipped sources", ""]
        out += [f"- `{s.file}` — {s.reason}" for s in review.skipped]
    return "\n".join(out) + "\n"


def render(review: Review, fmt: str, color: bool = False) -> str:
    if fmt == "json":
        return to_json(review)
    if fmt == "markdown":
        return to_markdown(review)
    return to_text(review, color=color)


def write(review: Review, out_dir: str | Path, now: datetime | None = None) -> list[Path]:
    """Write review-<timestamp>.json and .md into out_dir."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or review.created_at).strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"review-{stamp}.json"
    md_path = directory / f"review-{stamp}.md"
    json_path.write_text(to_json(review), encoding="utf-8")
    md_path.write_text(to_markdown(review), encoding="utf-8")
    return [json_path, md_path]


# ---------------------------------------------------------------- shared formatting


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


# ---------------------------------------------------------------- test suite


def suite_to_text(suite: TestSuite) -> str:
    out = [
        f"Test suite for {suite.target_file} -> {suite.test_file}",
        f"  iterations: {suite.iteration_count}",
        f"  cases: {suite.count(TestStatus.PASSED)} passed, {suite.count(TestStatus.XFAIL)} xfail, "
        f"{suite.count(TestStatus.DISCARDED)} discarded",
        f"  coverage: {_pct(suite.coverage_before)} -> {_pct(suite.coverage_after)}",
    ]
    for case in suite.cases:
        line = f"  [{case.status.value.upper()}] {case.name} ({case.intent.value}, {case.target})"
        if case.status is TestStatus.DISCARDED and case.failure_reason:
            line += f": {case.failure_reason.strip().splitlines()[0][:120]}"
        out.append(line)
    for finding in suite.bug_findings:
        out.append(f"  [BUG {finding.severity.value.upper()}] {_location(finding)} {finding.title}")
    out.extend(f"  warning: {warning}" for warning in suite.warnings)
    return "\n".join(out)


def write_suite(suite: TestSuite, out_dir: str | Path, now: datetime) -> list[Path]:
    """Write <out_dir>/<timestamp>/<test file> (when a test survived) and a suite JSON."""
    directory = Path(out_dir) / now.strftime("%Y%m%d-%H%M%S")
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    if suite.code:
        test_path = directory / suite.test_file
        test_path.write_text(suite.code, encoding="utf-8")
        paths.append(test_path)
    json_path = directory / f"{Path(suite.test_file).stem}.suite.json"
    json_path.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
    paths.append(json_path)
    return paths


# ---------------------------------------------------------------- quality report

_SCORE_LABELS = {
    "review": "Review findings",
    "coverage": "Coverage",
    "tests": "Passing tests",
    "static": "Lint + complexity",
}


def report_to_text(report: QualityReport) -> str:
    score = report.score
    out = [
        f"Quality report for {report.target}: {report.verdict.value.upper()} "
        f"(score {score.total:.1f}/100)",
        f"  review {_num(score.review)} | coverage {_num(score.coverage)} | "
        f"tests {_num(score.tests)} | lint+complexity {_num(score.static)}",
        f"  findings: {len(report.review.findings)}",
    ]
    out += [f"  note: {note}" for note in report.notes]
    return "\n".join(out)


def _recommendations(report: QualityReport) -> list[str]:
    tips = []
    counts = report.review.count_by_severity()
    urgent = counts[Severity.CRITICAL] + counts[Severity.HIGH]
    if urgent:
        tips.append(f"Fix the {urgent} critical/high finding(s) first.")
    low_cov = [c.file for c in report.coverage if c.percent < 80]
    if low_cov:
        tips.append("Raise coverage above 80% in: " + ", ".join(f"`{f}`" for f in low_cov))
    if report.test_run and report.test_run.failed:
        tips.append(f"Repair the {report.test_run.failed} failing test(s).")
    complex_files = [f"`{c.file}`" for c in report.complexity if c.worst_complexity > 10]
    if complex_files:
        tips.append("Split functions with complexity above 10 in: " + ", ".join(complex_files))
    return tips


def report_to_markdown(report: QualityReport) -> str:
    score = report.score
    out = [
        "# Quality Report",
        "",
        f"- Target: `{report.target}`",
        f"- Verdict: **{report.verdict.value.upper()}** — score **{score.total:.1f}/100**",
        f"- Model: `{report.review.model}`",
        f"- Created: {report.created_at.isoformat()}",
        "",
        "## Summary",
        "",
        report.review.summary,
        "",
        "## Score",
        "",
        "| Component | Score | Weight |",
        "|---|---|---|",
    ]
    for key, label in _SCORE_LABELS.items():
        weight = score.weights.get(key)
        weight_text = f"{weight * 100:.0f}%" if weight is not None else "not measured"
        out.append(f"| {label} | {_num(getattr(score, key))} | {weight_text} |")

    counts = report.review.count_by_severity()
    out += ["", "## Findings", "", ", ".join(f"{s.value}: {n}" for s, n in counts.items())]
    top = report.review.findings[:10]
    if top:
        out += ["", "| Severity | Category | Location | Title |", "|---|---|---|---|"]
        out += [
            f"| {f.severity.value} | {f.category.value} | `{_location(f)}` | {_cell(f.title)} |"
            for f in top
        ]
    if report.coverage:
        out += ["", "## Coverage", "", "| File | Coverage |", "|---|---|"]
        out += [f"| `{c.file}` | {c.percent:.1f}% |" for c in report.coverage]
    if report.test_run:
        run = report.test_run
        out += ["", "## Tests", "", f"- Passed: {run.passed}", f"- Failed: {run.failed}"]
        if run.timed_out:
            out.append("- Timed out: yes")
    for suite in report.test_suites:
        out += [
            "",
            f"### Generated: `{suite.test_file}` for `{suite.target_file}`",
            "",
            f"{suite.count(TestStatus.PASSED)} passed, {suite.count(TestStatus.XFAIL)} xfail, "
            f"{suite.count(TestStatus.DISCARDED)} discarded — coverage "
            f"{_pct(suite.coverage_before)} → {_pct(suite.coverage_after)}",
        ]
    if report.lint or report.complexity:
        out += [
            "",
            "## Static metrics",
            "",
            "| File | Lint violations | Avg complexity | Worst |",
            "|---|---|---|---|",
        ]
        lint = {r.file: r for r in report.lint}
        complexity = {r.file: r for r in report.complexity}
        for file in sorted(set(lint) | set(complexity)):
            lr, cr = lint.get(file), complexity.get(file)
            worst = f"`{cr.worst_name}` ({cr.worst_complexity})" if cr and cr.worst_name else "—"
            violations = lr.violation_count if lr else "n/a"
            average = cr.average if cr else "n/a"
            out.append(f"| `{file}` | {violations} | {average} | {worst} |")
    recommendations = _recommendations(report)
    if recommendations:
        out += ["", "## Recommendations", "", *(f"- {r}" for r in recommendations)]
    if report.notes:
        out += ["", "## Notes", "", *(f"- {note}" for note in report.notes)]
    return "\n".join(out) + "\n"


def render_report(report: QualityReport, fmt: str) -> str:
    if fmt == "json":
        return report.model_dump_json(indent=2)
    if fmt == "markdown":
        return report_to_markdown(report)
    return report_to_text(report)


def write_report(report: QualityReport, out_dir: str | Path) -> list[Path]:
    """Write report-<timestamp>.json and .md into out_dir."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"report-{stamp}.json"
    md_path = directory / f"report-{stamp}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(report_to_markdown(report), encoding="utf-8")
    return [json_path, md_path]
