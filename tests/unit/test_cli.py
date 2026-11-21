import io
import json
import logging

import pytest

from reviewer import cli
from reviewer.error import ApiError
from reviewer.model import Review
from tests.factory import FAKE_KEY, FakeClient, chunk_response, raw_finding


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Never load the developer's real env file during tests."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    return path


def _run(argv, client=None, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    client = client or FakeClient()
    code = cli.main(
        argv,
        client_factory=lambda args: client,
        stdin=io.StringIO(stdin),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def _client_with(severity):
    def handler(content):
        path = content.split('path="', 1)[1].split('"', 1)[0]
        return chunk_response([raw_finding(file=path, severity=severity)])

    return FakeClient(handler)


def test_clean_review_exits_zero(sample):
    code, out, _ = _run(["review", str(sample)])
    assert code == cli.EXIT_OK
    assert "0 finding(s)" in out


def test_high_finding_exits_one_by_default(sample):
    code, out, _ = _run(["review", str(sample)], _client_with("high"))
    assert code == cli.EXIT_FINDINGS
    assert "[HIGH]" in out


def test_fail_on_threshold_is_respected(sample):
    code, _, _ = _run(["review", str(sample), "--fail-on", "critical"], _client_with("high"))
    assert code == cli.EXIT_OK


def test_json_output_validates_against_review_schema(sample):
    _, out, _ = _run(["review", str(sample), "--format", "json"], _client_with("medium"))
    review = Review.model_validate_json(out)
    assert review.findings[0].severity.value == "medium"


def test_missing_target_exits_two(tmp_path):
    code, _, err = _run(["review", str(tmp_path / "nope.py")])
    assert code == cli.EXIT_USAGE
    assert "target not found" in err


def test_stdin_without_diff_flag_exits_two():
    code, _, err = _run(["review", "-"])
    assert code == cli.EXIT_USAGE
    assert "--diff" in err


def test_unknown_option_value_exits_two(sample):
    code, _, _ = _run(["review", str(sample), "--format", "xml"])
    assert code == cli.EXIT_USAGE


def test_api_error_exits_three_with_request_id(sample):
    client = FakeClient(lambda c: ApiError("Claude API error (status 500)", "req_42"))
    code, _, err = _run(["review", str(sample)], client)
    assert code == 3
    assert "req_42" in err


def test_diff_from_stdin_is_reviewed():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a = 1\n+b = 2\n"
    client = FakeClient()
    code, _, _ = _run(["review", "-", "--diff"], client, stdin=diff)
    assert code == cli.EXIT_OK
    assert 'path="x.py"' in client.calls[0]
    assert 'changed="2"' in client.calls[0]


def test_directory_target_reviews_every_file(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    code, out, _ = _run(["review", str(tmp_path), "--format", "json"])
    assert code == cli.EXIT_OK
    assert json.loads(out)["source_count"] == 2


def test_out_writes_json_and_markdown(sample, tmp_path):
    out_dir = tmp_path / "reports"
    code, _, err = _run(["review", str(sample), "--out", str(out_dir)])
    assert code == cli.EXIT_OK
    assert sorted(p.suffix for p in out_dir.iterdir()) == [".json", ".md"]
    assert "wrote" in err


def test_verbose_mode_never_prints_the_api_key(sample, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    code, out, err = _run(["review", str(sample), "--verbose"], _client_with("low"))
    logging.getLogger().handlers.clear()
    assert code == cli.EXIT_OK
    assert FAKE_KEY not in out
    assert FAKE_KEY not in err
