"""Framework-neutral predictor protocol and direct Python adapter."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    ActualUsage,
    ForecastReceipt,
    ForecastRequest,
    ReconciliationResult,
    SegmentForecast,
)


class PredictorAdapter(Protocol):
    def forecast(self, request: ForecastRequest) -> ForecastReceipt:
        ...

    def record_actual(self, usage: ActualUsage) -> ReconciliationResult:
        ...


class PythonPredictorAdapter:
    """Direct adapter for the in-workspace FutureTokenPredictor package."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._forecasts: dict[int, SegmentForecast] = {}

    def forecast(self, request: ForecastRequest) -> ForecastReceipt:
        from future_token_predictor import predict

        result = predict(description=request.description, db_path=self.db_path)
        if result.prediction_id is None:
            raise RuntimeError("prediction history is unavailable; cannot reconcile")

        forecast = SegmentForecast(
            prediction_id=result.prediction_id,
            segment=request.segment,
            model=result.model,
            provider=result.provider,
            archetype=result.archetype,
            prediction_method=result.prediction_method,
            expected_tokens=result.tokens_per_call.total,
            p5_tokens=result.tokens_p5,
            p50_tokens=result.tokens_p50,
            p95_tokens=result.tokens_p95,
            expected_cost_usd=result.cost_per_call.mean,
            cost_range_low_usd=result.cost_per_call.ci_95_low,
            cost_range_high_usd=result.cost_per_call.ci_95_high,
            cost_modeled_high_usd=result.cost_per_call.worst_case,
            bound_method=result.bound_method,
            bound_samples=result.bound_samples,
            bound_seed=result.bound_seed,
            pricing_verified=result.pricing_verified,
            pricing_timestamp=result.pricing_timestamp,
        )
        self._forecasts[forecast.prediction_id] = forecast
        return ForecastReceipt(
            run_id=request.run_id,
            workload_version=request.workload_version,
            golden_set_version=request.golden_set_version,
            observation_unit=request.observation_unit,
            forecasts=(forecast,),
            assumptions=request.assumptions,
            policy_candidate_id=request.policy_candidate_id,
            policy_candidate_version=request.policy_candidate_version,
            policy_candidate_content_hash=request.policy_candidate_content_hash,
        )

    def record_actual(self, usage: ActualUsage) -> ReconciliationResult:
        from future_token_predictor import record_actual

        per_task = usage.per_task()
        actual_total = (
            per_task.text_input
            + per_task.text_output
            + per_task.cached_input
            + per_task.image_input
            + per_task.document_input
            + per_task.audio_input
            + per_task.reasoning
        )
        outcome = record_actual(
            usage.prediction_id,
            actual_text_input=per_task.text_input,
            actual_text_output=per_task.text_output,
            actual_image_input=per_task.image_input,
            actual_document_input=per_task.document_input,
            actual_audio_input=per_task.audio_input,
            actual_reasoning=per_task.reasoning,
            actual_total=actual_total,
            actual_cost=per_task.cost_usd,
            db_path=self.db_path,
        )
        forecast = self._forecasts.get(usage.prediction_id)
        error = None
        if forecast is not None and forecast.expected_tokens > 0:
            error = 100.0 * (actual_total - forecast.expected_tokens) / forecast.expected_tokens
        return ReconciliationResult(
            prediction_id=usage.prediction_id,
            status=outcome.status,
            completed_tasks=usage.completed_tasks,
            actual_tokens_per_task=actual_total,
            forecast_error_pct=error,
        )