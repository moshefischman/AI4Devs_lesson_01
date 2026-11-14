from datetime import datetime, timezone

from reviewer.model import Finding, Review, SkippedSource, Usage
from reviewer.render import render, to_markdown, to_text, write

NOW = datetime(2026, 11, 14, 9, 30, 5, tzinfo=timezone.utc)


def _review(findings=None, skipped=None):
    return Review(
        findings=findings
        if findings is not None
        else [
            Finding(
                file="app.py",
                line_start=3,
                line_end=5,
                severity="high",
                category="bug",
                title="Pipe | in title",
                explanation="Breaks on empty list.",
                suggestion="if not items:\n    return 0",
            )
        ],
        summary="1 finding(s) across 1 source(s) (1 high).",
        model="fake-model",
        usage=Usage(),
        source_count=1,
        skipped=skipped or [],
        created_at=NOW,
    )


def test_text_shows_location_severity_and_suggestion():
    text = to_text(_review(skipped=[SkippedSource(file="b.py", reason="declined")]))
    assert "[HIGH] app.py:3-5 (bug) Pipe | in title" in text
    assert "    > if not items:" in text
    assert "[SKIPPED] b.py: declined" in text
    assert "\033[" not in text


def test_text_colors_only_when_requested():
    assert "\033[31m" in to_text(_review(), color=True)


def test_json_round_trips_through_the_review_schema():
    assert Review.model_validate_json(render(_review(), "json")) == _review()


def test_markdown_escapes_pipes_in_table_cells():
    markdown = to_markdown(_review())
    assert "| high | bug | `app.py:3-5` | Pipe \\| in title |" in markdown
    assert "```\nif not items:" in markdown


def test_markdown_without_findings_says_so():
    assert "No findings." in to_markdown(_review(findings=[]))


def test_write_creates_timestamped_json_and_markdown(tmp_path):
    paths = write(_review(), tmp_path / "out")
    assert [p.name for p in paths] == ["review-20261114-093005.json", "review-20261114-093005.md"]
    assert all(p.read_text(encoding="utf-8") for p in paths)
