"""Reconcile correlated gateway telemetry into predictor calibration actuals."""

from __future__ import annotations

from .contracts import ActualUsage, ForecastReceipt, ReconciliationResult
from .prediction import PredictorAdapter


class ReconciliationService:
    def __init__(self, adapter: PredictorAdapter) -> None:
        self.adapter = adapter
        self._processed: set[tuple[str, int]] = set()

    def reconcile(self, receipt: ForecastReceipt, records: list) -> list[ReconciliationResult]:
        results = []
        for forecast in receipt.forecasts:
            key = (receipt.run_id, forecast.prediction_id)
            if key in self._processed:
                results.append(ReconciliationResult(
                    prediction_id=forecast.prediction_id,
                    status="already_recorded",
                    completed_tasks=0,
                    actual_tokens_per_task=0.0,
                    detail="run and prediction were already reconciled",
                    segment_id=forecast.segment,
                ))
                continue

            matched = [
                record for record in records
                if record.run_id == receipt.run_id
                and record.prediction_id == forecast.prediction_id
                and record.model != "none"
            ]
            if not matched:
                results.append(ReconciliationResult(
                    prediction_id=forecast.prediction_id,
                    status="incomplete",
                    completed_tasks=0,
                    actual_tokens_per_task=0.0,
                    detail="no completed tasks found",
                    segment_id=forecast.segment,
                ))
                continue

            outcome = self.adapter.record_actual(ActualUsage(
                prediction_id=forecast.prediction_id,
                completed_tasks=len(matched),
                text_input=sum(record.input_tokens for record in matched),
                text_output=sum(record.output_tokens for record in matched),
                cached_input=sum(record.cached_tokens for record in matched),
                document_input=sum(record.document_tokens for record in matched),
                reasoning=sum(record.reasoning_tokens for record in matched),
                cost_usd=sum(record.cost_usd for record in matched),
            ))
            status = "recorded" if outcome.status == "updated" else outcome.status
            results.append(ReconciliationResult(
                prediction_id=outcome.prediction_id,
                status=status,
                completed_tasks=outcome.completed_tasks,
                actual_tokens_per_task=outcome.actual_tokens_per_task,
                forecast_error_pct=outcome.forecast_error_pct,
                detail=outcome.detail,
                segment_id=forecast.segment,
                task_ids=tuple(
                    record.task_id for record in matched if record.task_id
                ),
                trajectory_ids=tuple(
                    record.trajectory_id
                    for record in matched
                    if record.trajectory_id
                ),
            ))
            if status in {"recorded", "already_recorded"}:
                self._processed.add(key)
        return results