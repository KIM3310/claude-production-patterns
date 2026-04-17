"""Pattern 06 — Multi-Provider Fallback.

When Claude is slow or down, fall back to OpenAI or Bedrock. Requires:
    1. A common interface over multiple providers.
    2. Health checks on each provider.
    3. Circuit breaker to avoid hammering a degraded provider.
    4. Response-shape normalization.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock

log = logging.getLogger("claude_production.multi_provider")


# ===============================================================
# Common interface
# ===============================================================


@dataclass
class NormalizedResponse:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    raw: object  # underlying provider response


class Provider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 1024) -> NormalizedResponse:
        ...


class AnthropicProvider(Provider):
    name = "anthropic"
    model = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        from anthropic import Anthropic  # type: ignore
        self.client = Anthropic(api_key=api_key)
        if model:
            self.model = model

    def generate(self, prompt: str, max_tokens: int = 1024) -> NormalizedResponse:
        start = time.time()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        latency = int((time.time() - start) * 1000)
        text = response.content[0].text if response.content else ""
        return NormalizedResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency,
            raw=response,
        )


class OpenAIProvider(Provider):
    name = "openai"
    model = "gpt-4o"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        from openai import OpenAI  # type: ignore
        self.client = OpenAI(api_key=api_key)
        if model:
            self.model = model

    def generate(self, prompt: str, max_tokens: int = 1024) -> NormalizedResponse:
        start = time.time()
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        latency = int((time.time() - start) * 1000)
        text = response.choices[0].message.content or ""
        return NormalizedResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            latency_ms=latency,
            raw=response,
        )


# ===============================================================
# Circuit breaker
# ===============================================================


class CircuitState(str, Enum):
    CLOSED = "closed"  # normal operation
    OPEN = "open"  # provider is failing; skip
    HALF_OPEN = "half_open"  # tentative recovery test


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5  # consecutive failures to open
    recovery_timeout_seconds: float = 30.0  # time before trying half-open
    half_open_success_threshold: int = 2  # successes to close from half-open


class CircuitBreaker:
    """Per-provider circuit breaker."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_half_open_successes = 0
        self._opened_at: float | None = None
        self._lock = Lock()

    def is_available(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if (
                    self._opened_at is not None
                    and time.time() - self._opened_at >= self.config.recovery_timeout_seconds
                ):
                    self.state = CircuitState.HALF_OPEN
                    self._consecutive_half_open_successes = 0
                    return True
                return False
            if self.state == CircuitState.HALF_OPEN:
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._consecutive_half_open_successes += 1
                if (
                    self._consecutive_half_open_successes
                    >= self.config.half_open_success_threshold
                ):
                    self.state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    log.info(f"Circuit {self.name} closed")
            else:
                self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                self._opened_at = time.time()
                log.warning(f"Circuit {self.name} opened")
            elif self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self._opened_at = time.time()


# ===============================================================
# Multi-provider router
# ===============================================================


class AllProvidersFailed(RuntimeError):
    pass


class MultiProviderClient:
    """Routes requests across providers with fallback + circuit breaker."""

    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("At least one provider required")
        self.providers = providers
        self.breakers = {p.name: CircuitBreaker(p.name) for p in providers}

    def generate(self, prompt: str, max_tokens: int = 1024) -> NormalizedResponse:
        """Try providers in order. Returns first successful response."""
        last_exc: Exception | None = None

        for provider in self.providers:
            breaker = self.breakers[provider.name]
            if not breaker.is_available():
                log.info(f"Skipping {provider.name}: circuit open")
                continue

            try:
                response = provider.generate(prompt, max_tokens=max_tokens)
                breaker.record_success()
                return response
            except Exception as e:
                last_exc = e
                breaker.record_failure()
                log.warning(
                    f"{provider.name} failed",
                    extra={"error": str(e), "provider": provider.name},
                )

        raise AllProvidersFailed(f"All providers failed. Last: {last_exc}") from last_exc


if __name__ == "__main__":
    # Demo: mock two providers, simulate one failing
    class MockGoodProvider(Provider):
        name = "mock_good"
        model = "mock-1"

        def generate(self, prompt, max_tokens=1024):
            return NormalizedResponse("ok from good", self.name, self.model, 10, 5, 100, None)

    class MockFailingProvider(Provider):
        name = "mock_failing"
        model = "mock-2"

        def generate(self, prompt, max_tokens=1024):
            raise RuntimeError("simulated failure")

    client = MultiProviderClient([MockFailingProvider(), MockGoodProvider()])
    for _ in range(7):
        try:
            r = client.generate("test")
            print(f"Got: {r.text} (provider={r.provider})")
        except AllProvidersFailed as e:
            print(f"Failed: {e}")
