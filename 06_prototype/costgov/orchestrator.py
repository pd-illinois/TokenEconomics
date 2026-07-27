"""Shared application service for forecast, governed run, and reconciliation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .contracts import ExecutionContext, ForecastReceipt, ForecastRequest
from .evaluator import Evaluator
from .gateway import Gateway
from .prediction import PythonPredictorAdapter
from .reconciliation import ReconciliationService
from .telemetry import Telemetry


class StudioOrchestrator:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.artifacts = self.root / "studio_runs"
        self.artifacts.mkdir(exist_ok=True)

    def plan(self, run_id: str | None = None) -> tuple[ForecastReceipt, PythonPredictorAdapter]:
        run_id = run_id or str(uuid4())
        adapter = PythonPredictorAdapter(str(self.artifacts / "predictor_history.db"))
        forecasts = []
        assumptions = (
            "one prediction per workload segment",
            "actuals reconciled as average model tokens per completed task",
            "cache hits count as completed tasks with zero new model tokens",
        )
        descriptions = {
            "easy": "Simple GPT-4.1 customer support question with short context",
            "hard": "Complex GPT-4.1 support synthesis question with long document context",
        }
        for segment, description in descriptions.items():
            child = adapter.forecast(ForecastRequest(
                run_id=run_id,
                segment=segment,
                description=description,
                workload_version="support-workload-v1",
                golden_set_version="support-golden-v1",
                assumptions=assumptions,
            ))
            forecasts.extend(child.forecasts)
        return ForecastReceipt(
            run_id=run_id,
            workload_version="support-workload-v1",
            golden_set_version="support-golden-v1",
            observation_unit="completed_task",
            forecasts=tuple(forecasts),
            assumptions=assumptions,
        ), adapter

    def run(self, run_id: str, report_id: str, admission: dict) -> dict:
        if admission.get("status") != "admitted" or not admission.get("execution"):
            raise ValueError("an admitted policy binding is required")
        receipt, adapter = self.plan(run_id)
        config = self._load("config.json")
        execution = admission["execution"]
        config["routing"]["mode"] = execution["routing_mode"]
        config["semantic_cache"].update(execution["semantic_cache"])
        config["budgets"].update({
            "per_tenant_usd_per_run": execution["budget"]["per_tenant_usd_per_run"],
            "hard_cap_action": execution["budget"]["hard_cap_action"],
        })
        config["evaluation"].update(execution["evaluation"])
        config["evaluation"]["sample_rate"] = 1.0
        workload = self._load("workload.json")
        expanded_workload = list(self._expand(workload))
        telemetry = Telemetry(sample_rate=1.0)
        gateway = Gateway(config, telemetry)
        by_segment = {forecast.segment: forecast for forecast in receipt.forecasts}

        for tenant, question, segment in expanded_workload:
            forecast = by_segment[segment]
            gateway.handle(tenant, question, segment, ExecutionContext(
                run_id=receipt.run_id,
                prediction_id=forecast.prediction_id,
                segment=segment,
                policy_version=admission["policy"]["version"],
                report_id=report_id,
            ))

        evaluator = Evaluator(str(self.root / "golden_set.json"))
        quality = evaluator.continuous(telemetry.sampled)
        reconciliation = ReconciliationService(adapter).reconcile(receipt, telemetry.records)
        result = {
            "run_id": receipt.run_id,
            "report_id": report_id,
            "status": "completed",
            "forecast": asdict(receipt),
            "policy": {
                **admission["policy"],
                "handoff_id": admission["handoff_id"],
                "plan_id": admission["plan_id"],
                "receipt_id": admission["receipt_id"],
                "routing_mode": execution["routing_mode"],
                "quality_floor": config["evaluation"]["min_quality"],
                "selection_reason": "execution settings loaded from the admitted policy revision",
            },
            "observed": {
                "requests": len(telemetry.records),
                "cost_usd": telemetry.total_cost(),
                "avg_latency_ms": telemetry.avg_latency(),
                "cache_hits": sum(record.cache_hit for record in telemetry.records),
                "input_tokens": sum(record.input_tokens for record in telemetry.records),
                "output_tokens": sum(record.output_tokens for record in telemetry.records),
                "quality": quality.mean_score,
                "quality_by_segment": quality.by_difficulty,
            },
            "reconciliation": [asdict(item) for item in reconciliation],
        }
        run_dir = self.artifacts / receipt.run_id
        run_dir.mkdir(exist_ok=True)
        (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        telemetry.dump_jsonl(str(run_dir / "telemetry.jsonl"))
        return result

    def _load(self, name: str) -> dict:
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    @staticmethod
    def _expand(workload: dict):
        for request in workload["requests"]:
            variants = [request["question"], *request.get("paraphrases", [])]
            for index in range(request["repeats"]):
                yield (
                    request["tenant"],
                    variants[index % len(variants)],
                    request["difficulty"],
                )