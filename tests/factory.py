"""Small builders and a fake Claude client shared by the unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from reviewer.chunk import estimate_tokens
from reviewer.claude_client import ChunkResponse, ChunkResult, RawFinding
from reviewer.model import Usage
from reviewer.source import Source

FAKE_KEY = "sk-ant-test-0000-not-a-real-key"


def make_source(path: str = "app.py", count: int = 5, changed: set[int] | None = None) -> Source:
    lines = tuple((n, f"line_{n} = {n}") for n in range(1, count + 1))
    return Source(path=path, lines=lines, changed_lines=frozenset(changed or set()))


def raw_finding(**overrides: Any) -> RawFinding:
    data = {
        "file": "app.py",
        "line_start": 2,
        "line_end": 2,
        "severity": "high",
        "category": "bug",
        "title": "Off-by-one",
        "explanation": "Loop skips the last element.",
        "suggestion": "",
    }
    data.update(overrides)
    return RawFinding(**data)


def chunk_response(
    findings: list[RawFinding] | None = None,
    summary: str = "Looks fine.",
    usage: Usage | None = None,
) -> ChunkResponse:
    return ChunkResponse(
        result=ChunkResult(findings=findings or [], summary=summary),
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        request_id="req_test",
    )


Handler = Callable[[str], Any]


class FakeClient:
    """Stands in for ClaudeClient. `handler(content)` returns a ChunkResponse or an exception."""

    model = "fake-model"

    def __init__(self, handler: Handler | None = None, tokens: int | None = None):
        self.handler = handler or (lambda content: chunk_response())
        self.tokens = tokens
        self.calls: list[str] = []

    def review_chunk(self, system: str, content: str) -> ChunkResponse:
        self.calls.append(content)
        result = self.handler(content)
        if isinstance(result, Exception):
            raise result
        return result

    def count_tokens(self, system: str, content: str) -> int:
        return self.tokens if self.tokens is not None else estimate_tokens(content)


def sdk_message(
    payload: dict[str, Any] | str | None = None,
    stop_reason: str = "end_turn",
    stop_details: Any = None,
) -> SimpleNamespace:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload or {"findings": [], "summary": "ok"})
    )
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=0,
        ),
    )
    message._request_id = "req_sdk"
    return message


class _Stream:
    def __init__(self, message: Any, request_id: str | None = None):
        self._message = message
        self.request_id = request_id

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self._message


class FakeSdk:
    """Mimics the parts of anthropic.Anthropic that ClaudeClient uses."""

    def __init__(
        self,
        message: Any = None,
        error: Exception | None = None,
        tokens: int = 42,
        stream_request_id: str | None = None,
    ):
        self.params: dict[str, Any] = {}
        self.count_params: dict[str, Any] = {}
        self._message = message if message is not None else sdk_message()
        self._error = error
        self._tokens = tokens
        self._stream_request_id = stream_request_id
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.messages = SimpleNamespace(count_tokens=self._count_tokens)

    def _stream(self, **params: Any) -> _Stream:
        self.params = params
        if self._error:
            raise self._error
        return _Stream(self._message, self._stream_request_id)

    def _count_tokens(self, **params: Any) -> SimpleNamespace:
        self.count_params = params
        if self._error:
            raise self._error
        return SimpleNamespace(input_tokens=self._tokens)
