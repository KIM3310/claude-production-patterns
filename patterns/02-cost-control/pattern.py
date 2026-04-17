"""Pattern 02 — Cost Control for Claude production.

Three mechanisms:
    1. Session-level spend cap (prevent runaway in a single workflow)
    2. Daily/monthly spend cap (prevent runaway over time)
    3. Cost anomaly detection (alert on unusual spend)
    4. Per-tenant attribution (know where cost is going)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

log = logging.getLogger("claude_production.cost_control")


# ===============================================================
# Claude pricing (April 2026 reference; update via config)
# ===============================================================

PRICING_USD_PER_MTOK = {
    "claude-sonnet-4-20250514": {
        "input": 3.00,
        "cache_write": 3.75,  # ephemeral cache write ~25% more
        "cache_read": 0.30,   # 90% discount
        "output": 15.00,
    },
    "claude-haiku-4-20250807": {
        "input": 0.25,
        "cache_write": 0.3125,
        "cache_read": 0.025,
        "output": 1.25,
    },
    "claude-sonnet-3-5-20241022": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
}


def cost_for_request(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Compute cost in USD for a single request."""
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        log.warning("Unknown model pricing", extra={"model": model})
        return 0.0

    # Regular input (non-cached)
    regular_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    cost = (regular_input / 1_000_000) * pricing["input"]
    cost += (cache_read_tokens / 1_000_000) * pricing["cache_read"]
    cost += (cache_write_tokens / 1_000_000) * pricing["cache_write"]
    cost += (output_tokens / 1_000_000) * pricing["output"]
    return cost


# ===============================================================
# Budget tracker
# ===============================================================


class BudgetExceeded(Exception):
    def __init__(self, scope: str, spent: float, limit: float) -> None:
        super().__init__(f"{scope} budget exceeded: ${spent:.4f} > ${limit:.4f}")
        self.scope = scope
        self.spent = spent
        self.limit = limit


@dataclass
class BudgetLimits:
    session_usd: float | None = None
    daily_usd: float | None = None
    monthly_usd: float | None = None


class BudgetTracker:
    """Tracks spend at session, daily, monthly granularity.

    Thread-safe. Raises BudgetExceeded when a limit is breached.
    """

    def __init__(self, limits: BudgetLimits) -> None:
        self.limits = limits
        self._session_spend = 0.0
        self._daily_spend: dict[str, float] = defaultdict(float)
        self._monthly_spend: dict[str, float] = defaultdict(float)
        self._per_tenant_spend: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def charge(
        self,
        cost: float,
        tenant: str | None = None,
        date: dt.date | None = None,
    ) -> None:
        """Record a cost. Raises BudgetExceeded if any limit is now breached."""
        if date is None:
            date = dt.date.today()
        day_key = date.isoformat()
        month_key = date.isoformat()[:7]

        with self._lock:
            self._session_spend += cost
            self._daily_spend[day_key] += cost
            self._monthly_spend[month_key] += cost
            if tenant:
                self._per_tenant_spend[tenant] += cost

            if self.limits.session_usd is not None:
                if self._session_spend > self.limits.session_usd:
                    raise BudgetExceeded(
                        "session", self._session_spend, self.limits.session_usd
                    )

            if self.limits.daily_usd is not None:
                if self._daily_spend[day_key] > self.limits.daily_usd:
                    raise BudgetExceeded(
                        "daily", self._daily_spend[day_key], self.limits.daily_usd
                    )

            if self.limits.monthly_usd is not None:
                if self._monthly_spend[month_key] > self.limits.monthly_usd:
                    raise BudgetExceeded(
                        "monthly",
                        self._monthly_spend[month_key],
                        self.limits.monthly_usd,
                    )

    def stats(self) -> dict:
        today = dt.date.today().isoformat()
        month = today[:7]
        with self._lock:
            return {
                "session_spend_usd": self._session_spend,
                "today_spend_usd": self._daily_spend[today],
                "month_spend_usd": self._monthly_spend[month],
                "per_tenant_spend_usd": dict(self._per_tenant_spend),
            }


# ===============================================================
# Anomaly detection
# ===============================================================


class CostAnomalyDetector:
    """Detect anomalous cost using rolling baseline + z-score."""

    def __init__(
        self,
        window_days: int = 7,
        z_threshold: float = 3.0,
    ) -> None:
        self.window_days = window_days
        self.z_threshold = z_threshold
        self._daily_costs: deque[tuple[dt.date, float]] = deque(maxlen=window_days + 1)

    def record_daily_cost(self, cost: float, date: dt.date | None = None) -> bool:
        """Record daily cost. Returns True if this day is anomalous vs prior days."""
        date = date or dt.date.today()

        # Compute baseline from prior days (not including today)
        prior = [c for d, c in self._daily_costs if d < date]
        self._daily_costs.append((date, cost))

        if len(prior) < 3:
            return False  # insufficient baseline

        mean = sum(prior) / len(prior)
        variance = sum((c - mean) ** 2 for c in prior) / len(prior)
        stddev = variance**0.5

        if stddev == 0:
            return cost > mean * 2  # simple doubling rule if no variance

        z = (cost - mean) / stddev
        return z > self.z_threshold


# ===============================================================
# Cost attribution logger
# ===============================================================


class CostAttributionLogger:
    """Log cost per request with tenant, feature, model attribution.

    Writes JSONL that can be ingested into a warehouse for analysis.
    """

    def __init__(self, log_path: str) -> None:
        self.log_path = log_path
        self._lock = threading.Lock()

    def log(
        self,
        *,
        request_id: str,
        tenant: str | None,
        feature: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float,
        latency_ms: int,
    ) -> None:
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "request_id": request_id,
            "tenant": tenant,
            "feature": feature,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost_usd": round(cost_usd, 6),
            "latency_ms": latency_ms,
        }
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")


# ===============================================================
# Integrated cost-controlled client
# ===============================================================


class CostControlledClient:
    """Claude client wrapper enforcing budget limits + attribution."""

    def __init__(
        self,
        api_key: str,
        limits: BudgetLimits,
        log_path: str = "/tmp/claude_cost.jsonl",
    ) -> None:
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError("anthropic SDK required") from e

        self.client = Anthropic(api_key=api_key)
        self.budget = BudgetTracker(limits)
        self.logger = CostAttributionLogger(log_path)

    def send(
        self,
        *,
        model: str,
        messages: list,
        tenant: str | None = None,
        feature: str | None = None,
        **kwargs,
    ):
        import time
        import uuid

        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start = time.time()
        response = self.client.messages.create(model=model, messages=messages, **kwargs)
        latency_ms = int((time.time() - start) * 1000)

        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        cost = cost_for_request(
            model, input_tokens, output_tokens, cache_read, cache_write
        )

        # Record attribution (even if charge raises — we want the log)
        self.logger.log(
            request_id=request_id,
            tenant=tenant,
            feature=feature,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

        self.budget.charge(cost, tenant=tenant)
        return response


if __name__ == "__main__":
    # Demonstrate cost computation
    cost = cost_for_request(
        "claude-sonnet-4-20250514",
        input_tokens=3500,
        output_tokens=800,
        cache_read_tokens=2000,
    )
    print(f"Cost for typical request: ${cost:.6f}")

    cost_bulk = cost_for_request(
        "claude-sonnet-4-20250514",
        input_tokens=3500,
        output_tokens=800,
    )
    print(f"Cost without caching:     ${cost_bulk:.6f}")
    print(f"Caching saves:            ${cost_bulk - cost:.6f} ({(1 - cost/cost_bulk)*100:.1f}%)")

    # Demonstrate budget tracker
    tracker = BudgetTracker(BudgetLimits(daily_usd=1.00, session_usd=0.10))
    try:
        for i in range(20):
            tracker.charge(cost, tenant=f"tenant_{i%3}")
    except BudgetExceeded as e:
        print(f"\n{e}")
    print(f"Stats: {tracker.stats()}")
