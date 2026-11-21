"""Reviewer error hierarchy. Each class maps to one CLI exit code."""


class ReviewerError(Exception):
    """Base class for every error the reviewer raises on purpose."""

    exit_code = 2


class InputError(ReviewerError):
    """Bad target, empty input, or an input limit was exceeded."""

    exit_code = 2


class ApiError(ReviewerError):
    """The Claude API call failed after the SDK's own retries."""

    exit_code = 3

    def __init__(self, message: str, request_id: str | None = None):
        super().__init__(message)
        self.request_id = request_id


class RefusalError(ApiError):
    """Claude (and the fallback chain, if enabled) declined the request."""

    def __init__(self, category: str | None = None, request_id: str | None = None):
        super().__init__(f"request declined (category: {category or 'unknown'})", request_id)
        self.category = category


class OutputLimitError(ApiError):
    """The response hit max_tokens before the JSON was complete."""


class InvalidResponseError(ApiError):
    """The response did not validate against the expected schema."""


class RunnerError(ReviewerError):
    """pytest could not run at all (missing, crashed, or usage error)."""

    exit_code = 4
