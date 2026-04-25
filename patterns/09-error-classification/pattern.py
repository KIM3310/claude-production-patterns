"""Pattern 09 — Error Classification.

Classify LLM call errors into: retryable transient / retryable rate limit /
fail-fast permanent. Correct classification drives correct retry behavior.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

log = logging.getLogger("claude_production.error_classification")

T = TypeVar("T")


class ErrorClass(str, Enum):
    TRANSIENT = "transient"  # retry with backoff
    RATE_LIMIT = "rate_limit"  # retry with specific retry-after
    PERMANENT_SERVER = "permanent_server"  # fail fast, likely Anthropic issue
    PERMANENT_CLIENT = "permanent_client"  # fail fast, bug in our request
    AUTH = "auth"  # fail fast, credential problem
    QUOTA = "quota"  # fail fast, account quota exhausted
    CONTENT = "content"  # fail fast, content policy violation
    TIMEOUT = "timeout"  # retry with smaller request
    UNKNOWN = "unknown"  # retry once, then fail


def classify_error(exc: Exception) -> tuple[ErrorClass, float | None]:
    """Classify an exception. Returns (class, retry_after_seconds or None)."""
    cls_name = exc.__class__.__name__
    msg = str(exc).lower()

    # Anthropic SDK specific
    if cls_name == "RateLimitError":
        # extract retry-after if present
        retry_after = getattr(exc, "retry_after", None) or 60.0
        return ErrorClass.RATE_LIMIT, retry_after

    if cls_name == "APIStatusError":
        status = getattr(exc, "status_code", 0)
        if status == 401 or status == 403:
            return ErrorClass.AUTH, None
        if status == 402:
            return ErrorClass.QUOTA, None
        if status == 400:
            # Could be validation error or content policy
            if "content" in msg and "policy" in msg:
                return ErrorClass.CONTENT, None
            return ErrorClass.PERMANENT_CLIENT, None
        if status == 404:
            return ErrorClass.PERMANENT_CLIENT, None
        if 500 <= status < 600:
            return ErrorClass.TRANSIENT, None

    if cls_name in ("APIConnectionError", "APITimeoutError", "ConnectionError"):
        return ErrorClass.TRANSIENT, None

    if cls_name == "APIResponseValidationError":
        return ErrorClass.PERMANENT_CLIENT, None

    if "timeout" in msg:
        return ErrorClass.TIMEOUT, None

    if "rate" in msg and "limit" in msg:
        return ErrorClass.RATE_LIMIT, 60.0

    if "quota" in msg or "exceeded" in msg:
        return ErrorClass.QUOTA, None

    return ErrorClass.UNKNOWN, None


@dataclass
class RetryDecision:
    should_retry: bool
    delay_seconds: float
    reason: str


def retry_decision(
    error_class: ErrorClass,
    retry_after: float | None,
    attempt: int,
    max_attempts: int = 5,
) -> RetryDecision:
    """Decide whether to retry based on error class + attempt number."""
    if attempt >= max_attempts:
        return RetryDecision(False, 0, f"max attempts ({max_attempts}) reached")

    if error_class == ErrorClass.RATE_LIMIT:
        return RetryDecision(
            True,
            retry_after or 60.0,
            f"rate limit; retry in {retry_after or 60.0}s",
        )

    if error_class == ErrorClass.TRANSIENT:
        return RetryDecision(
            True,
            min(32.0, 2.0**attempt),
            "transient error; exponential backoff",
        )

    if error_class == ErrorClass.TIMEOUT:
        if attempt < 2:
            return RetryDecision(True, 1.0, "timeout; single retry")
        return RetryDecision(False, 0, "timeout after retries; giving up")

    if error_class == ErrorClass.UNKNOWN:
        if attempt < 1:
            return RetryDecision(True, 2.0, "unknown error; single retry")
        return RetryDecision(False, 0, "unknown error persists; failing")

    # PERMANENT_CLIENT, PERMANENT_SERVER, AUTH, QUOTA, CONTENT
    return RetryDecision(False, 0, f"{error_class.value} not retryable")


def call_with_classification(
    fn: Callable[[], T],
    max_attempts: int = 5,
) -> T:
    """Execute fn with classification-aware retry."""
    for attempt in range(max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            error_class, retry_after = classify_error(e)
            decision = retry_decision(error_class, retry_after, attempt, max_attempts)

            log.warning(
                "error.classified",
                extra={
                    "attempt": attempt,
                    "error_class": error_class.value,
                    "should_retry": decision.should_retry,
                    "delay_s": decision.delay_seconds,
                    "reason": decision.reason,
                    "error": str(e),
                },
            )

            if not decision.should_retry:
                raise
            time.sleep(decision.delay_seconds)

    raise RuntimeError("retries exhausted")


if __name__ == "__main__":
    # Demo classification
    class FakeAnthropicError(Exception):
        pass

    class RateLimitError(FakeAnthropicError):
        retry_after = 30.0

    class APIStatusError(FakeAnthropicError):
        def __init__(self, status_code: int, msg: str) -> None:
            super().__init__(msg)
            self.status_code = status_code

    test_errors = [
        RateLimitError("rate limit"),
        APIStatusError(401, "invalid api key"),
        APIStatusError(500, "internal server error"),
        APIStatusError(400, "malformed request"),
        APIStatusError(400, "content policy violation"),
        TimeoutError("request timed out"),
        ValueError("random error"),
    ]

    for err in test_errors:
        cls, ra = classify_error(err)
        decision = retry_decision(cls, ra, attempt=0)
        print(f"{type(err).__name__:25s} → {cls.value:25s} retry={decision.should_retry:5} delay={decision.delay_seconds:5.1f}s")
