"""Render a Review as text, JSON, or Markdown, and write it to disk."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .model import Finding, Review, Severity

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
