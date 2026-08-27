"""Immutable contracts shared by prediction, governance, and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ForecastRequest:
    run_id: str
    segment: str
    description: str
    workload_version: str
    golden_set_version: str
    observation_unit: str = "completed_task"
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SegmentForecast:
    prediction_id: int
    segment: str
    model: str
    provider: str
    archetype: str
    prediction_method: str
    expected_tokens: float
    p5_tokens: float
    p50_tokens: float
    p95_tokens: float
    expected_cost_usd: float
    cost_range_low_usd: float
    cost_range_high_usd: float
    cost_modeled_high_usd: float
    bound_method: str
    bound_samples: int
    bound_seed: Optional[int]
    pricing_verified: bool
    pricing_timestamp: Optional[str]


@dataclass(frozen=True)
class ForecastReceipt:
    run_id: str
    workload_version: str
    golden_set_version: str
    observation_unit: str
    forecasts: tuple[SegmentForecast, ...]
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanReceipt:
    """Immutable snapshot handed from Studio planning to TokenGov admission."""

    receipt_id: str
    report_id: str
    plan_id: str
    schema_version: str
    created_at: str
    description: str
    intake_json: str
    analysis_json: str
    confirmed_profile_json: str
    assumptions_json: str
    clarifications_json: str
    exclusions_json: str
    prediction_json: str
    infrastructure_json: str
    content_hash: str


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    prediction_id: int
    segment: str
    policy_version: str
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    report_id: Optional[str] = None
    task_id: Optional[str] = None
    trajectory_id: Optional[str] = None
    task_created_at: Optional[str] = None
    trajectory_started_at: Optional[str] = None
    workload_id: Optional[str] = None
    workload_version: Optional[str] = None
    segment_id: Optional[str] = None
    segment_version: Optional[str] = None
    prediction_receipt_id: Optional[str] = None
    prediction_receipt_hash: Optional[str] = None
    policy_id: Optional[str] = None
    policy_hash: Optional[str] = None
    policy_source: Optional[str] = None
    policy_label: Optional[str] = None
    policy_etag: Optional[str] = None


@dataclass(frozen=True)
class ActualUsage:
    prediction_id: int
    completed_tasks: int
    text_input: float = 0.0
    text_output: float = 0.0
    cached_input: float = 0.0
    image_input: float = 0.0
    document_input: float = 0.0
    audio_input: float = 0.0
    reasoning: float = 0.0
    cost_usd: Optional[float] = None

    def __post_init__(self) -> None:
        if self.completed_tasks < 1:
            raise ValueError("completed_tasks must be at least 1")
        values = (
            self.text_input,
            self.text_output,
            self.cached_input,
            self.image_input,
            self.document_input,
            self.audio_input,
            self.reasoning,
        )
        if any(value < 0 for value in values):
            raise ValueError("actual token values must be non-negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")

    def per_task(self) -> "ActualUsage":
        count = self.completed_tasks
        return ActualUsage(
            prediction_id=self.prediction_id,
            completed_tasks=1,
            text_input=self.text_input / count,
            text_output=self.text_output / count,
            cached_input=self.cached_input / count,
            image_input=self.image_input / count,
            document_input=self.document_input / count,
            audio_input=self.audio_input / count,
            reasoning=self.reasoning / count,
            cost_usd=self.cost_usd / count if self.cost_usd is not None else None,
        )


@dataclass(frozen=True)
class ReconciliationResult:
    prediction_id: int
    status: str
    completed_tasks: int
    actual_tokens_per_task: float
    forecast_error_pct: Optional[float] = None
    detail: str = ""
    segment_id: str = ""
    task_ids: tuple[str, ...] = ()
    trajectory_ids: tuple[str, ...] = ()