"""Pattern 05 — Canary Rollout with Automatic Rollback.

Shift traffic gradually to a new prompt/model version. Monitor SLO.
Automatic rollback on regression.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class CanaryStage(str, Enum):
    NEW = "new"
    DARK_LAUNCH = "dark_launch"  # evaluate but don't serve
    CANARY_5 = "canary_5"
    CANARY_25 = "canary_25"
    CANARY_50 = "canary_50"
    FULL = "full"
    ROLLED_BACK = "rolled_back"


@dataclass
class StageConfig:
    stage: CanaryStage
    traffic_fraction: float
    duration_minutes: int
    slo_checks: list[Callable[[dict], tuple[bool, str]]] = field(default_factory=list)


@dataclass
class RolloutConfig:
    prompt_id: str
    baseline_version: str
    new_version: str
    stages: list[StageConfig]
    error_budget_burn_threshold: float = 2.0  # 2x baseline error rate triggers rollback
    eval_regression_threshold: float = 0.03  # 3% drop triggers rollback


class CanaryRollout:
    """Run a canary rollout with SLO checks and automatic rollback."""

    def __init__(
        self,
        config: RolloutConfig,
        metrics_source: Callable[[str, str, int], dict],
        router_set_split: Callable[[str, dict], None],
        logger: Callable[[str, dict], None] = lambda msg, ctx: print(f"{msg}: {ctx}"),
    ) -> None:
        self.config = config
        self.metrics_source = metrics_source
        self.router_set_split = router_set_split
        self.logger = logger
        self.current_stage: CanaryStage = CanaryStage.NEW

    def run(self) -> dict:
        """Execute the rollout. Returns final state."""
        history = []
        self.logger("canary.starting", {"prompt_id": self.config.prompt_id})

        for stage_config in self.config.stages:
            self.logger(
                "canary.entering_stage",
                {"stage": stage_config.stage.value, "fraction": stage_config.traffic_fraction},
            )

            # Apply traffic split
            new_fraction = stage_config.traffic_fraction
            baseline_fraction = 1.0 - new_fraction
            self.router_set_split(
                self.config.prompt_id,
                {
                    self.config.baseline_version: baseline_fraction,
                    self.config.new_version: new_fraction,
                },
            )

            # Observe for stage duration
            stage_start = time.time()
            ok = True
            reason = None
            while time.time() - stage_start < stage_config.duration_minutes * 60:
                metrics = self.metrics_source(
                    self.config.prompt_id,
                    self.config.new_version,
                    60,  # last 60 seconds
                )
                for check in stage_config.slo_checks:
                    check_ok, check_reason = check(metrics)
                    if not check_ok:
                        ok = False
                        reason = check_reason
                        break
                if not ok:
                    break

                time.sleep(10)  # sleep between checks

            history.append(
                {
                    "stage": stage_config.stage.value,
                    "ok": ok,
                    "reason": reason,
                    "duration": time.time() - stage_start,
                }
            )

            if not ok:
                self.logger("canary.rollback", {"stage": stage_config.stage.value, "reason": reason})
                self._rollback()
                return {"status": "rolled_back", "history": history, "reason": reason}

            self.current_stage = stage_config.stage

        self.logger("canary.complete", {"prompt_id": self.config.prompt_id})
        return {"status": "complete", "history": history}

    def _rollback(self) -> None:
        self.router_set_split(
            self.config.prompt_id,
            {self.config.baseline_version: 1.0, self.config.new_version: 0.0},
        )
        self.current_stage = CanaryStage.ROLLED_BACK


# Standard SLO checks


def check_error_rate(baseline_error_rate: float, multiplier: float = 2.0):
    def _check(metrics: dict) -> tuple[bool, str | None]:
        current = metrics.get("error_rate", 0.0)
        if current > baseline_error_rate * multiplier:
            return False, f"error rate {current:.3f} exceeds {multiplier}x baseline {baseline_error_rate:.3f}"
        return True, None

    return _check


def check_latency_p95(baseline_p95_ms: float, multiplier: float = 1.5):
    def _check(metrics: dict) -> tuple[bool, str | None]:
        current = metrics.get("latency_p95_ms", 0)
        if current > baseline_p95_ms * multiplier:
            return False, f"p95 latency {current}ms exceeds {multiplier}x baseline"
        return True, None

    return _check


def check_eval_score(baseline_score: float, max_drop: float = 0.03):
    def _check(metrics: dict) -> tuple[bool, str | None]:
        current = metrics.get("eval_score", baseline_score)
        if current < baseline_score - max_drop:
            return False, f"eval score {current:.3f} dropped more than {max_drop} from baseline"
        return True, None

    return _check


# Standard stage recipe


def standard_6_stage_rollout(
    prompt_id: str,
    baseline_version: str,
    new_version: str,
    baseline_error_rate: float = 0.02,
    baseline_p95_ms: float = 2000,
    baseline_eval_score: float = 0.85,
) -> RolloutConfig:
    """Standard 6-stage canary rollout: dark launch → 5% → 25% → 50% → 100%."""
    checks = [
        check_error_rate(baseline_error_rate),
        check_latency_p95(baseline_p95_ms),
        check_eval_score(baseline_eval_score),
    ]

    return RolloutConfig(
        prompt_id=prompt_id,
        baseline_version=baseline_version,
        new_version=new_version,
        stages=[
            StageConfig(CanaryStage.DARK_LAUNCH, 0.0, 60, checks),  # evaluate via shadow eval first
            StageConfig(CanaryStage.CANARY_5, 0.05, 60, checks),
            StageConfig(CanaryStage.CANARY_25, 0.25, 60, checks),
            StageConfig(CanaryStage.CANARY_50, 0.50, 60, checks),
            StageConfig(CanaryStage.FULL, 1.0, 1440, checks),  # 24-hour full observation
        ],
    )


if __name__ == "__main__":
    # Demo with mock metrics source
    def mock_metrics(prompt_id: str, version: str, window_s: int) -> dict:
        return {
            "error_rate": 0.015,
            "latency_p95_ms": 1800,
            "eval_score": 0.87,
        }

    def mock_router_set(prompt_id: str, split: dict) -> None:
        print(f"Router: {prompt_id} → {split}")

    config = standard_6_stage_rollout(
        prompt_id="contract_summary",
        baseline_version="v3",
        new_version="v4",
    )
    # Shorten for demo
    for stage in config.stages:
        stage.duration_minutes = 0.05  # 3 seconds

    rollout = CanaryRollout(config, mock_metrics, mock_router_set)
    result = rollout.run()
    print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
