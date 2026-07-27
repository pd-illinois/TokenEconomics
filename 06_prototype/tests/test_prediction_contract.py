from __future__ import annotations

import pytest

from costgov.contracts import ActualUsage, ForecastRequest
from costgov.prediction import PythonPredictorAdapter
from future_token_predictor.history import HistoryDatabase


def test_forecast_to_actual_round_trip_uses_per_task_units(tmp_path):
    db_path = str(tmp_path / "history.db")
    adapter = PythonPredictorAdapter(db_path)
    receipt = adapter.forecast(ForecastRequest(
        run_id="run-001",
        segment="factual_lookup",
        description="Simple GPT-4.1 RAG question over a document",
        workload_version="workload-v1",
        golden_set_version="golden-v1",
    ))
    forecast = receipt.forecasts[0]

    result = adapter.record_actual(ActualUsage(
        prediction_id=forecast.prediction_id,
        completed_tasks=2,
        text_input=1200,
        text_output=400,
        document_input=200,
        cost_usd=0.04,
    ))

    assert receipt.observation_unit == "completed_task"
    assert result.status == "updated"
    assert result.actual_tokens_per_task == 900
    record = HistoryDatabase(db_path).get_record(forecast.prediction_id)
    assert record is not None
    assert record.actual_text_input == 600
    assert record.actual_text_output == 200
    assert record.actual_document_input == 100
    assert record.actual_total == 900
    assert record.actual_cost == pytest.approx(0.02)


def test_actual_usage_requires_completed_task():
    with pytest.raises(ValueError, match="completed_tasks"):
        ActualUsage(prediction_id=1, completed_tasks=0)