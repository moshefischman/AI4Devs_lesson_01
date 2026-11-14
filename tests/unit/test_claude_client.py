import logging
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from reviewer.claude_client import (
    CHUNK_RESULT_SCHEMA,
    FALLBACK_BETA,
    ClaudeClient,
    parse_message,
)
from reviewer.error import ApiError, InvalidResponseError, OutputLimitError, RefusalError
from tests.factory import FAKE_KEY, FakeSdk, sdk_message

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status):
    response = httpx2.Response(status, request=REQUEST, headers={"request-id": "req_err"})
    return cls("boom", response=response, body=None)


def _finding_payload(**overrides):
    finding = {
        "file": "app.py",
        "line_start": 1,
        "line_end": 1,
        "severity": "low",
        "category": "style",
        "title": "Name",
        "explanation": "Unclear name.",
        "suggestion": "",
    }
    finding.update(overrides)
    return {"findings": [finding], "summary": "One nit."}


def test_parse_message_reads_text_block_and_usage():
    response = parse_message(sdk_message(_finding_payload()))
    assert response.result.findings[0].title == "Name"
    assert response.result.summary == "One nit."
    assert response.usage.input_tokens == 100
    assert response.usage.cache_read_input_tokens == 0
    assert response.request_id == "req_sdk"


def test_parse_message_raises_refusal_with_category():
    message = sdk_message(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(RefusalError) as info:
        parse_message(message)
    assert info.value.category == "cyber"


def test_parse_message_raises_on_max_tokens():
    with pytest.raises(OutputLimitError):
        parse_message(sdk_message('{"findings": [', stop_reason="max_tokens"))


@pytest.mark.parametrize(
    "payload",
    ["not json", _finding_payload(severity="urgent"), {"summary": "missing findings"}],
)
def test_parse_message_rejects_schema_violations(payload):
    with pytest.raises(InvalidResponseError):
        parse_message(sdk_message(payload))


def test_review_chunk_sends_structured_output_request_with_fallback():
    sdk = FakeSdk()
    client = ClaudeClient(model="claude-opus-5-5", effort="high", sdk=sdk)

    client.review_chunk("SYSTEM", "<file>")

    params = sdk.params
    assert params["model"] == "claude-opus-5-5"
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": CHUNK_RESULT_SCHEMA},
    }
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"] == [{"role": "user", "content": "<file>"}]
    assert params["betas"] == [FALLBACK_BETA]
    assert params["fallbacks"] == "default"


def test_review_chunk_without_fallback_omits_beta_params():
    sdk = FakeSdk()
    ClaudeClient(fallback=False, sdk=sdk).review_chunk("S", "C")
    assert "betas" not in sdk.params
    assert "fallbacks" not in sdk.params


def test_invalid_effort_is_rejected():
    with pytest.raises(ValueError):
        ClaudeClient(effort="extreme", sdk=FakeSdk())


def test_count_tokens_uses_model_and_returns_input_tokens():
    sdk = FakeSdk(tokens=1234)
    assert ClaudeClient(model="m", sdk=sdk).count_tokens("S", "C") == 1234
    assert sdk.count_params["model"] == "m"


@pytest.mark.parametrize(
    "error,expected",
    [
        (_status_error(anthropic.AuthenticationError, 401), "authentication failed"),
        (_status_error(anthropic.NotFoundError, 404), "not found"),
        (_status_error(anthropic.RateLimitError, 429), "rate limited"),
        (_status_error(anthropic.BadRequestError, 400), "request rejected"),
        (_status_error(anthropic.InternalServerError, 500), "status 500"),
        (anthropic.APIConnectionError(request=REQUEST), "network error"),
    ],
)
def test_sdk_errors_become_api_errors(error, expected):
    client = ClaudeClient(sdk=FakeSdk(error=error))
    with pytest.raises(ApiError, match=expected) as info:
        client.review_chunk("S", "C")
    assert info.value.exit_code == 3


def test_api_error_keeps_request_id():
    client = ClaudeClient(sdk=FakeSdk(error=_status_error(anthropic.InternalServerError, 500)))
    with pytest.raises(ApiError) as info:
        client.review_chunk("S", "C")
    assert info.value.request_id == "req_err"


def test_api_key_never_appears_in_debug_logs(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    caplog.set_level(logging.DEBUG)

    ClaudeClient(sdk=FakeSdk(sdk_message(_finding_payload()))).review_chunk("S", "C")

    assert "req_sdk" in caplog.text
    assert FAKE_KEY not in caplog.text


def test_review_chunk_takes_request_id_from_the_stream():
    sdk = FakeSdk(stream_request_id="req_stream")
    assert ClaudeClient(sdk=sdk).review_chunk("S", "C").request_id == "req_stream"
