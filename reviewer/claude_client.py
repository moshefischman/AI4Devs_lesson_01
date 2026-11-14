"""The only module that talks to the Anthropic SDK.

Credentials resolve through the SDK (env var, `.env` loaded by the CLI, or an
`ant auth login` profile). This module never reads, prints, or logs the key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .error import (
    ApiError,
    InvalidResponseError,
    OutputLimitError,
    RefusalError,
    ReviewerError,
)
from .model import Category, Severity, Usage

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MAX_TOKENS = 32_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

log = logging.getLogger(__name__)

CHUNK_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "severity": {"type": "string", "enum": [s.value for s in Severity]},
                    "category": {"type": "string", "enum": [c.value for c in Category]},
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": [
                    "file",
                    "line_start",
                    "line_end",
                    "severity",
                    "category",
                    "title",
                    "explanation",
                    "suggestion",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["findings", "summary"],
    "additionalProperties": False,
}


class RawFinding(BaseModel):
    """A finding exactly as Claude returned it, before line/path verification."""

    file: str
    line_start: int
    line_end: int
    severity: Severity
    category: Category
    title: str
    explanation: str
    suggestion: str = ""


class ChunkResult(BaseModel):
    findings: list[RawFinding]
    summary: str


ResultT = TypeVar("ResultT", bound=BaseModel)


@dataclass
class StructuredResponse(Generic[ResultT]):
    """A schema-validated response: `result` is an instance of the requested model."""

    result: ResultT
    usage: Usage
    request_id: str | None = None


ChunkResponse = StructuredResponse[ChunkResult]


class ReviewClient(Protocol):
    """What the review service needs; tests provide a fake."""

    model: str

    def review_chunk(self, system: str, content: str) -> ChunkResponse: ...

    def count_tokens(self, system: str, content: str) -> int: ...


def _usage_from(message: Any) -> Usage:
    usage = getattr(message, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def parse_message(
    message: Any,
    request_id: str | None = None,
    result_type: type[ResultT] = ChunkResult,  # type: ignore[assignment]
) -> StructuredResponse[ResultT]:
    """Check stop_reason first, then validate the JSON text against `result_type`."""
    request_id = request_id or getattr(message, "_request_id", None)
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise RefusalError(getattr(details, "category", None), request_id)
    if stop_reason == "max_tokens":
        raise OutputLimitError("response hit max_tokens", request_id)

    text = "".join(block.text for block in getattr(message, "content", []) if block.type == "text")
    try:
        result = result_type.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise InvalidResponseError(f"response did not match the schema: {exc}", request_id) from exc
    return StructuredResponse(result=result, usage=_usage_from(message), request_id=request_id)


@dataclass
class ClaudeClient:
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    fallback: bool = True
    max_tokens: int = DEFAULT_MAX_TOKENS
    sdk: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.effort not in EFFORTS:
            raise ValueError(f"effort must be one of {EFFORTS}")
        if self.sdk is None:
            try:
                self.sdk = anthropic.Anthropic()
            except anthropic.AnthropicError as exc:
                raise ReviewerError(
                    "no Anthropic credentials found: set ANTHROPIC_API_KEY or run `ant auth login`"
                ) from exc

    def _system(self, system: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def review_chunk(self, system: str, content: str) -> ChunkResponse:
        return self.complete(system, content, CHUNK_RESULT_SCHEMA, ChunkResult)

    def complete(
        self,
        system: str,
        content: str,
        schema: dict[str, Any],
        result_type: type[ResultT],
    ) -> StructuredResponse[ResultT]:
        """One streaming request whose output is constrained to `schema`."""
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self._system(system),
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if self.fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        with _translate_errors():
            with self.sdk.beta.messages.stream(**params) as stream:
                message = stream.get_final_message()
                # Streamed messages carry no _request_id; the stream exposes it.
                request_id = getattr(stream, "request_id", None)

        response = parse_message(message, request_id, result_type)
        log.debug(
            "%s received: request_id=%s input=%d output=%d cache_read=%d",
            result_type.__name__,
            response.request_id,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.usage.cache_read_input_tokens,
        )
        return response

    def count_tokens(self, system: str, content: str) -> int:
        with _translate_errors():
            result = self.sdk.messages.count_tokens(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        return result.input_tokens


@contextmanager
def _translate_errors() -> Iterator[None]:
    """Map SDK exceptions (already retried by the SDK) to ApiError, most specific first."""
    try:
        yield
    except anthropic.AnthropicError as exc:
        request_id = getattr(exc, "request_id", None)
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            message = "authentication failed: set ANTHROPIC_API_KEY or run `ant auth login`"
        elif isinstance(exc, anthropic.NotFoundError):
            message = "model or endpoint not found: check --model"
        elif isinstance(exc, anthropic.RateLimitError):
            message = "rate limited by the Claude API after retries; try again later"
        elif isinstance(exc, anthropic.BadRequestError):
            message = f"request rejected by the Claude API: {exc.message}"
        elif isinstance(exc, anthropic.APIStatusError):
            message = f"Claude API error (status {exc.status_code})"
        elif isinstance(exc, anthropic.APIConnectionError):
            message = "network error while calling the Claude API"
        else:
            message = f"Claude API error: {type(exc).__name__}"
        raise ApiError(message, request_id) from exc
