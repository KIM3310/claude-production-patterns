"""Pattern 01 — Rate Limiting & Backoff for Claude production.

Three layers:
    1. Per-tenant token bucket (prevents one tenant starving others)
    2. Global rate limiter (stays below Anthropic's limits)
    3. Retry with exponential backoff + jitter (handles 429s)
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

log = logging.getLogger("claude_production.rate_limiting")

T = TypeVar("T")


# ===============================================================
# Layer 1: Per-tenant token bucket
# ===============================================================


@dataclass
class TokenBucket:
    """Standard token bucket. Thread-safe.

    rate: tokens added per second
    capacity: max tokens bucket can hold (burst size)
    """

    rate: float  # tokens per second
    capacity: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def consume(self, n: float = 1.0) -> bool:
        """Try to consume n tokens. Returns True if allowed, False if denied."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= n:
                self.tokens -= n
                return True
            return False

    def available(self) -> float:
        """Current token count without consuming."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            return min(self.capacity, self.tokens + elapsed * self.rate)


class TenantRateLimiter:
    """Per-tenant rate limiting.

    Usage:
        limiter = TenantRateLimiter(default_rate=10, default_capacity=30)
        if limiter.check("tenant_abc", cost=1.0):
            # proceed
        else:
            raise TenantQuotaExceeded(...)
    """

    def __init__(
        self,
        default_rate: float = 10.0,  # requests/sec per tenant
        default_capacity: float = 30.0,  # burst size
    ) -> None:
        self.default_rate = default_rate
        self.default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._tenant_overrides: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def set_tenant_limit(self, tenant: str, rate: float, capacity: float) -> None:
        """Set a custom rate/capacity for a specific tenant."""
        with self._lock:
            self._tenant_overrides[tenant] = (rate, capacity)
            if tenant in self._buckets:
                # Reset bucket to new capacity
                self._buckets[tenant] = TokenBucket(rate=rate, capacity=capacity)

    def check(self, tenant: str, cost: float = 1.0) -> bool:
        """Check if tenant can consume `cost` tokens."""
        bucket = self._get_or_create(tenant)
        return bucket.consume(cost)

    def available(self, tenant: str) -> float:
        return self._get_or_create(tenant).available()

    def _get_or_create(self, tenant: str) -> TokenBucket:
        with self._lock:
            if tenant not in self._buckets:
                rate, capacity = self._tenant_overrides.get(
                    tenant, (self.default_rate, self.default_capacity)
                )
                self._buckets[tenant] = TokenBucket(rate=rate, capacity=capacity)
            return self._buckets[tenant]


# ===============================================================
# Layer 2: Global rate limiter
# ===============================================================


class GlobalRateLimiter:
    """Global (cluster-wide) rate limiter.

    Tracks requests-per-minute against Anthropic's limits. When approaching
    the limit, starts delaying new requests. When at the limit, rejects.
    """

    def __init__(
        self,
        requests_per_minute: int = 4000,  # Anthropic tier 4 typical
        tokens_per_minute: int = 1_000_000,
        soft_limit_fraction: float = 0.8,
        hard_limit_fraction: float = 0.95,
    ) -> None:
        self.rpm_limit = requests_per_minute
        self.tpm_limit = tokens_per_minute
        self.soft_fraction = soft_limit_fraction
        self.hard_fraction = hard_limit_fraction

        self._request_times: list[float] = []
        self._token_usage: list[tuple[float, int]] = []  # (time, tokens)
        self._lock = threading.Lock()

    def check(self, estimated_tokens: int) -> bool:
        """Check if request is allowed. Returns True if allowed."""
        with self._lock:
            self._evict_old()

            current_rpm = len(self._request_times)
            current_tpm = sum(t for _, t in self._token_usage)

            if current_rpm >= self.rpm_limit * self.hard_fraction:
                return False
            if current_tpm + estimated_tokens >= self.tpm_limit * self.hard_fraction:
                return False

            # If in soft-limit zone, add a small delay
            if current_rpm >= self.rpm_limit * self.soft_fraction:
                # Compute recommended delay based on how close we are
                overage = (current_rpm - self.rpm_limit * self.soft_fraction) / (
                    self.rpm_limit * (self.hard_fraction - self.soft_fraction)
                )
                delay = overage * 2.0  # up to 2 second delay at hard limit
                time.sleep(delay)

            self._request_times.append(time.monotonic())
            self._token_usage.append((time.monotonic(), estimated_tokens))
            return True

    def _evict_old(self) -> None:
        cutoff = time.monotonic() - 60.0  # 1 minute window
        self._request_times = [t for t in self._request_times if t >= cutoff]
        self._token_usage = [(t, c) for (t, c) in self._token_usage if t >= cutoff]

    def stats(self) -> dict:
        with self._lock:
            self._evict_old()
            current_rpm = len(self._request_times)
            current_tpm = sum(t for _, t in self._token_usage)
            return {
                "rpm_current": current_rpm,
                "rpm_limit": self.rpm_limit,
                "rpm_utilization": current_rpm / self.rpm_limit,
                "tpm_current": current_tpm,
                "tpm_limit": self.tpm_limit,
                "tpm_utilization": current_tpm / self.tpm_limit,
            }


# ===============================================================
# Layer 3: Retry with backoff + jitter
# ===============================================================


class TenantQuotaExceeded(Exception):
    def __init__(self, tenant: str, available: float) -> None:
        super().__init__(
            f"Tenant {tenant} exceeded quota. Available tokens: {available:.2f}"
        )
        self.tenant = tenant
        self.available = available


class GlobalRateLimitExceeded(Exception):
    pass


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 32.0,
    retryable_errors: tuple[type[Exception], ...] = (Exception,),
    jitter: bool = True,
) -> T:
    """Execute fn with exponential backoff on retryable errors.

    Retries with backoff: base * 2^attempt, capped at max_delay, with jitter.

    Usage:
        from anthropic import RateLimitError

        result = retry_with_backoff(
            lambda: client.messages.create(...),
            retryable_errors=(RateLimitError,),
        )
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except retryable_errors as e:
            last_exc = e
            if attempt == max_attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**attempt))
            if jitter:
                delay *= 0.5 + random.random()  # 0.5x–1.5x jitter
            log.warning(
                "retry.backoff",
                extra={
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "delay_s": delay,
                    "error": str(e),
                },
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


# ===============================================================
# Integrated: a rate-limited Claude client wrapper
# ===============================================================


class RateLimitedClaudeClient:
    """Wrapper combining all three layers.

    Usage:
        client = RateLimitedClaudeClient(api_key=...)
        response = client.messages_create(
            tenant="tenant_abc",
            model="claude-sonnet-4-20250514",
            messages=[...],
            max_tokens=1024,
        )
    """

    def __init__(
        self,
        api_key: str,
        tenant_limiter: TenantRateLimiter | None = None,
        global_limiter: GlobalRateLimiter | None = None,
    ) -> None:
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError("anthropic SDK required") from e

        self.client = Anthropic(api_key=api_key)
        self.tenant_limiter = tenant_limiter or TenantRateLimiter()
        self.global_limiter = global_limiter or GlobalRateLimiter()

    def messages_create(self, *, tenant: str, **kwargs: object) -> object:
        # Layer 1: tenant bucket
        if not self.tenant_limiter.check(tenant, cost=1.0):
            raise TenantQuotaExceeded(tenant, self.tenant_limiter.available(tenant))

        # Layer 2: global limiter
        estimated_tokens = kwargs.get("max_tokens", 1024)
        if not self.global_limiter.check(estimated_tokens=estimated_tokens):  # type: ignore[arg-type]
            raise GlobalRateLimitExceeded("Global rate limit exceeded")

        # Layer 3: retry with backoff
        try:
            from anthropic import RateLimitError, APIStatusError  # type: ignore
        except ImportError:
            RateLimitError = Exception  # type: ignore[assignment]
            APIStatusError = Exception  # type: ignore[assignment]

        def _call():
            return self.client.messages.create(**kwargs)

        return retry_with_backoff(
            _call,
            max_attempts=5,
            retryable_errors=(RateLimitError, APIStatusError),
        )


if __name__ == "__main__":
    # Simple smoke test with mocked client
    tl = TenantRateLimiter(default_rate=2, default_capacity=5)
    gl = GlobalRateLimiter(requests_per_minute=100, tokens_per_minute=10000)

    print("TenantRateLimiter:")
    for i in range(10):
        allowed = tl.check("tenant_test", cost=1.0)
        print(f"  request {i}: {'ALLOWED' if allowed else 'DENIED'}")

    print("\nGlobalRateLimiter:")
    for i in range(5):
        allowed = gl.check(estimated_tokens=500)
        print(f"  request {i}: {'ALLOWED' if allowed else 'DENIED'}")
    print(f"  stats: {gl.stats()}")
