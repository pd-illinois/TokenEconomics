from __future__ import annotations

from costgov.contracts import ExecutionContext, ForecastRequest
from costgov.gateway import Gateway
from costgov.prediction import PythonPredictorAdapter
from costgov.reconciliation import ReconciliationService
from costgov.telemetry import Telemetry
from future_token_predictor.history import HistoryDatabase


def _config():
    return {
        "semantic_cache": {"enabled": True, "score_threshold": 0.8},
        "routing": {"mode": "balanced"},
        "budgets": {
            "per_tenant_usd_per_run": 1.0,
            "hard_cap_action": "degrade",
        },
        "context": {"prune": True, "max_context_items": 3},
    }


def test_reconciliation_groups_completed_tasks_and_is_idempotent(tmp_path):
    db_path = str(tmp_path / "history.db")
    adapter = PythonPredictorAdapter(db_path)
    receipt = adapter.forecast(ForecastRequest(
        run_id="run-001",
        segment="easy",
        description="Simple GPT-4.1 support question",
        workload_version="workload-v1",
        golden_set_version="golden-v1",
    ))
    prediction_id = receipt.forecasts[0].prediction_id
    telemetry = Telemetry(sample_rate=0.0)
    gateway = Gateway(_config(), telemetry)
    execution = ExecutionContext(
        run_id="run-001",
        prediction_id=prediction_id,
        segment="easy",
        policy_version="policy-v1",
    )
    first = gateway.handle("tenant-a", "Where is my order", "easy", execution)
    second = gateway.handle("tenant-a", "Where is my order", "easy", execution)
    assert second.cache_hit is True

    service = ReconciliationService(adapter)
    reconciled = service.reconcile(receipt, telemetry.records)
    duplicate = service.reconcile(receipt, telemetry.records)

    assert reconciled[0].status == "recorded"
    assert reconciled[0].completed_tasks == 2
    assert reconciled[0].actual_tokens_per_task == (
        first.input_tokens + first.output_tokens
    ) / 2
    assert duplicate[0].status == "already_recorded"

    record = HistoryDatabase(db_path).get_record(prediction_id)
    assert record is not None
    assert record.actual_total == reconciled[0].actual_tokens_per_task


def test_reconciliation_reports_incomplete_without_completed_tasks(tmp_path):
    adapter = PythonPredictorAdapter(str(tmp_path / "history.db"))
    receipt = adapter.forecast(ForecastRequest(
        run_id="run-001",
        segment="easy",
        description="Simple GPT-4.1 support question",
        workload_version="workload-v1",
        golden_set_version="golden-v1",
    ))

    result = ReconciliationService(adapter).reconcile(receipt, [])

    assert result[0].status == "incomplete"