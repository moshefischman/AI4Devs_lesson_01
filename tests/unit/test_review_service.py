from datetime import datetime, timezone

import pytest

from reviewer.error import InputError, InvalidResponseError, OutputLimitError, RefusalError
from reviewer.model import Severity, Usage
from reviewer.review_service import ReviewLimit, ReviewOptions, deduplicate, review
from tests.factory import FakeClient, chunk_response, make_source, raw_finding

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def test_findings_are_verified_and_sorted_by_severity():
    client = FakeClient(
        lambda content: chunk_response(
            [
                raw_finding(severity="low", line_start=1, line_end=1, title="Nit"),
                raw_finding(severity="critical", line_start=3, line_end=4, title="Crash"),
                raw_finding(severity="high", line_start=99, line_end=99, title="Ghost line"),
                raw_finding(file="other.py", title="Ghost file"),
            ]
        )
    )

    result = review([make_source("app.py", 5)], client, now=NOW)

    assert [f.title for f in result.findings] == ["Crash", "Nit"]
    assert result.dropped_finding_count == 2
    assert result.created_at == NOW
    assert result.model == "fake-model"


def test_deduplicate_keeps_the_most_severe_copy():
    findings = review(
        [make_source()],
        FakeClient(
            lambda c: chunk_response(
                [
                    raw_finding(severity="low", title="Same"),
                    raw_finding(severity="high", title="same "),
                ]
            )
        ),
    ).findings
    assert len(findings) == 1
    assert findings[0].severity is Severity.HIGH
    assert deduplicate([]) == []


def test_min_severity_filters_lower_findings():
    client = FakeClient(
        lambda c: chunk_response(
            [raw_finding(severity="low"), raw_finding(severity="high", title="B")]
        )
    )
    result = review([make_source()], client, ReviewOptions(min_severity=Severity.MEDIUM))
    assert [f.severity for f in result.findings] == [Severity.HIGH]


def _per_file_handler(content):
    findings = [
        raw_finding(file=path, title=f"Issue in {path}")
        for path in ("a.py", "b.py")
        if f'path="{path}"' in content
    ]
    return chunk_response(
        findings, summary="chunk ok", usage=Usage(input_tokens=7, output_tokens=3)
    )


def test_findings_from_multiple_chunks_are_merged_and_usage_summed():
    client = FakeClient(_per_file_handler)
    sources = [make_source("a.py", 40), make_source("b.py", 40)]

    result = review(sources, client, ReviewOptions(chunk_tokens=500))

    assert len(client.calls) == 2
    assert {f.file for f in result.findings} == {"a.py", "b.py"}
    assert result.usage == Usage(input_tokens=14, output_tokens=6)
    assert result.summary.startswith("2 finding(s) across 2 source(s) (2 high).")


def test_refused_chunk_is_reported_as_skipped_and_others_continue():
    def handler(content):
        if 'path="a.py"' in content:
            return RefusalError("cyber")
        return _per_file_handler(content)

    result = review(
        [make_source("a.py", 40), make_source("b.py", 40)],
        FakeClient(handler),
        ReviewOptions(chunk_tokens=500),
    )

    assert [s.file for s in result.skipped] == ["a.py"]
    assert "cyber" in result.skipped[0].reason
    assert [f.file for f in result.findings] == ["b.py"]


def test_invalid_response_is_reported_as_skipped():
    result = review([make_source()], FakeClient(lambda c: InvalidResponseError("bad")))
    assert result.skipped[0].reason == "invalid response from Claude"
    assert result.findings == []


def test_max_tokens_splits_the_chunk_and_retries():
    def handler(content):
        if 'path="a.py"' in content and 'path="b.py"' in content:
            return OutputLimitError("too long")
        return _per_file_handler(content)

    client = FakeClient(handler)
    result = review([make_source("a.py"), make_source("b.py")], client)

    assert len(client.calls) == 3
    assert {f.file for f in result.findings} == {"a.py", "b.py"}
    assert result.skipped == []


def test_persistent_max_tokens_ends_as_skipped():
    result = review([make_source("a.py", 1)], FakeClient(lambda c: OutputLimitError("too long")))
    assert result.skipped[0].file == "a.py"
    assert "too long" in result.skipped[0].reason


def test_empty_input_is_rejected():
    with pytest.raises(InputError, match="no reviewable"):
        review([], FakeClient())


def test_too_many_files_is_rejected_before_any_call():
    client = FakeClient()
    with pytest.raises(InputError, match="exceed"):
        review(
            [make_source(f"{i}.py") for i in range(3)],
            client,
            ReviewOptions(limit=ReviewLimit(max_files=2)),
        )
    assert client.calls == []


def test_token_limit_is_checked_before_any_call():
    client = FakeClient(tokens=500_000)
    with pytest.raises(InputError, match="tokens"):
        review([make_source()], client)
    assert client.calls == []
