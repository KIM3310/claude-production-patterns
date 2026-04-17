"""Pattern 08 — Observability for Claude agents.

Structured logging + OpenTelemetry traces + Prometheus metrics.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


# ===============================================================
# Structured logging setup
# ===============================================================


def setup_logging(service_name: str = "claude-agent", level: str = "INFO") -> logging.Logger:
    """Set up structured JSON logging."""
    import json

    class JSONFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            log_data = {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
                "level": record.levelname,
                "service": service_name,
                "logger": record.name,
                "message": record.getMessage(),
            }
            # Add extra fields
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "created", "filename", "funcName",
                    "levelname", "levelno", "lineno", "module", "msecs",
                    "pathname", "process", "processName", "relativeCreated",
                    "thread", "threadName", "getMessage",
                ):
                    log_data[key] = value
            return json.dumps(log_data, default=str)

    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    return logging.getLogger(service_name)


# ===============================================================
# OpenTelemetry tracing
# ===============================================================


def setup_tracing(service_name: str = "claude-agent"):
    """Set up OpenTelemetry tracer. Returns a tracer instance."""
    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore

        resource = Resource(attributes={SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(OTLPSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        return trace.get_tracer(service_name)
    except ImportError:
        logging.warning("OpenTelemetry not installed; tracing disabled")
        return None


# ===============================================================
# Prometheus metrics
# ===============================================================


class Metrics:
    """Convenience wrapper over prometheus_client for agent-specific metrics."""

    def __init__(self) -> None:
        try:
            from prometheus_client import Counter, Histogram, Gauge  # type: ignore

            self.agent_runs_total = Counter(
                "agent_runs_total",
                "Total agent runs",
                ["terminal_reason"],
            )
            self.agent_run_steps = Histogram(
                "agent_run_steps",
                "Number of steps per agent run",
                buckets=[1, 2, 3, 4, 5, 6, 8, 10, 15, 20],
            )
            self.llm_latency_seconds = Histogram(
                "llm_latency_seconds",
                "LLM call latency",
                ["model", "step_index"],
                buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
            )
            self.tool_call_total = Counter(
                "tool_call_total",
                "Tool call count",
                ["tool_name", "outcome"],
            )
            self.llm_tokens_total = Counter(
                "llm_tokens_total",
                "LLM tokens consumed",
                ["type"],  # input, output, cache_read, cache_write
            )
            self.llm_cost_usd_total = Counter(
                "llm_cost_usd_total",
                "Cumulative LLM cost",
                ["model"],
            )
            self.active_runs = Gauge("active_agent_runs", "Active agent runs")
            self._available = True
        except ImportError:
            logging.warning("prometheus_client not installed; metrics disabled")
            self._available = False

    def record_run_end(self, terminal_reason: str, steps: int) -> None:
        if not self._available:
            return
        self.agent_runs_total.labels(terminal_reason=terminal_reason).inc()
        self.agent_run_steps.observe(steps)

    def record_llm_call(
        self,
        model: str,
        step_index: int,
        latency_seconds: float,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        if not self._available:
            return
        self.llm_latency_seconds.labels(model=model, step_index=str(step_index)).observe(
            latency_seconds
        )
        self.llm_tokens_total.labels(type="input").inc(input_tokens)
        self.llm_tokens_total.labels(type="output").inc(output_tokens)
        self.llm_cost_usd_total.labels(model=model).inc(cost_usd)

    def record_tool_call(self, tool_name: str, outcome: str) -> None:
        if not self._available:
            return
        self.tool_call_total.labels(tool_name=tool_name, outcome=outcome).inc()


# ===============================================================
# Combined observable agent decorator
# ===============================================================


@dataclass
class AgentSpan:
    request_id: str
    user_id: str | None = None
    tenant_id: str | None = None


@contextmanager
def observe_agent_run(metrics: Metrics, tracer, span: AgentSpan, logger: logging.Logger):
    """Context manager that instruments an agent run."""
    start = time.time()
    log_ctx = {"request_id": span.request_id, "user_id": span.user_id, "tenant_id": span.tenant_id}
    logger.info("agent.run.start", extra=log_ctx)
    metrics.active_runs.inc() if metrics._available else None

    state = {"terminal_reason": "error", "steps": 0}

    try:
        if tracer:
            with tracer.start_as_current_span("agent.run") as otel_span:
                otel_span.set_attribute("request_id", span.request_id)
                yield state
        else:
            yield state
    except Exception as e:
        logger.exception("agent.run.failed", extra={**log_ctx, "error": str(e)})
        state["terminal_reason"] = "error"
        raise
    finally:
        duration = time.time() - start
        metrics.record_run_end(state["terminal_reason"], state["steps"])
        if metrics._available:
            metrics.active_runs.dec()
        logger.info(
            "agent.run.end",
            extra={
                **log_ctx,
                "duration_s": duration,
                "terminal_reason": state["terminal_reason"],
                "steps": state["steps"],
            },
        )


if __name__ == "__main__":
    logger = setup_logging()
    metrics = Metrics()
    tracer = setup_tracing()

    span = AgentSpan(request_id="req_abc123", user_id="user_42")
    with observe_agent_run(metrics, tracer, span, logger) as state:
        # Do work
        state["steps"] = 3
        state["terminal_reason"] = "converged"
        metrics.record_llm_call("claude-sonnet-4-20250514", 1, 1.2, 350, 80, 0.002)
        metrics.record_tool_call("query_sql", "success")
        time.sleep(0.1)
