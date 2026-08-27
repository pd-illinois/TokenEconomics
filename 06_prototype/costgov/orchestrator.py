"""Shared application service for forecast, governed run, and reconciliation."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .contracts import ExecutionContext, ForecastReceipt, ForecastRequest
from .evaluator import Evaluator
from .gateway import Gateway
from .prediction import PythonPredictorAdapter
from .reconciliation import ReconciliationService
from .telemetry import Telemetry
from .trajectory_contracts import (
    EvidenceField,
    SegmentIdentity,
    StepEvidence,
    StepKind,
    StepStatus,
    TaskIdentity,
    TrajectoryContractBinding,
    TrajectoryEnvelope,
    TrajectoryStore,
)


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
        trajectory_contract = TrajectoryContractBinding.from_dict(
            admission.get("trajectory_contract")
        )
        if (
            trajectory_contract.prediction_binding is None
            or trajectory_contract.policy_binding is None
        ):
            raise ValueError(
                "execution requires prediction and policy trajectory bindings"
            )

        for index, (tenant, question, segment) in enumerate(expanded_workload, 1):
            forecast = by_segment[segment]
            task_id = f"task-{uuid5(NAMESPACE_URL, f'{run_id}:task:{index}').hex}"
            trajectory_id = (
                f"trajectory-"
                f"{uuid5(NAMESPACE_URL, f'{run_id}:trajectory:{index}').hex}"
            )
            started_at = datetime.now(timezone.utc).isoformat()
            gateway.handle(tenant, question, segment, ExecutionContext(
                run_id=receipt.run_id,
                prediction_id=forecast.prediction_id,
                segment=segment,
                policy_version=admission["policy"]["version"],
                report_id=report_id,
                task_id=task_id,
                trajectory_id=trajectory_id,
                task_created_at=started_at,
                trajectory_started_at=started_at,
                workload_id=trajectory_contract.workload.workload_id,
                workload_version=trajectory_contract.workload.version,
                segment_id=segment,
                segment_version=trajectory_contract.segment_schema_version,
                prediction_receipt_id=(
                    trajectory_contract.prediction_binding.receipt_id
                ),
                prediction_receipt_hash=(
                    trajectory_contract.prediction_binding.content_hash
                ),
                policy_id=trajectory_contract.policy_binding.policy_id,
                policy_hash=trajectory_contract.policy_binding.content_hash,
                policy_source=trajectory_contract.policy_binding.source,
                policy_label=trajectory_contract.policy_binding.label,
                policy_etag=trajectory_contract.policy_binding.etag,
            ))

        evaluator = Evaluator(str(self.root / "golden_set.json"))
        quality = evaluator.continuous(telemetry.sampled)
        reconciliation = ReconciliationService(adapter).reconcile(receipt, telemetry.records)
        outcome_by_task = {item.task_id: item for item in quality.outcomes}
        trajectory_store = TrajectoryStore(
            self.artifacts / receipt.run_id / "trajectories"
        )
        trajectory_evidence = []
        for record in telemetry.records:
            outcome = outcome_by_task.get(record.task_id)
            child_kind = (
                StepKind.CACHE
                if record.cache_hit
                else StepKind.MODEL
                if record.model != "none"
                else StepKind.TOOL
            )
            child_status = (
                StepStatus.SKIPPED
                if record.model == "none"
                else StepStatus.COMPLETED
            )
            root_step_id = f"step-{record.task_id[5:]}-iteration"
            child_step_id = f"step-{record.task_id[5:]}-work"
            evidence = (
                EvidenceField.from_value("model", record.model),
                EvidenceField.from_value("cost_usd", record.cost_usd),
                EvidenceField.from_value("input_tokens", record.input_tokens),
                EvidenceField.from_value("output_tokens", record.output_tokens),
                EvidenceField.from_value("cache_hit", record.cache_hit),
                EvidenceField.from_value(
                    "evaluation_score",
                    outcome.score if outcome is not None else None,
                ),
                EvidenceField.from_value("evidence_status", "simulated"),
            )
            envelope = TrajectoryEnvelope(
                schema_version=trajectory_contract.schema_version,
                trajectory_id=record.trajectory_id,
                run_id=record.run_id,
                trace_id=record.trace_id,
                task=TaskIdentity(
                    task_id=record.task_id,
                    report_id=record.report_id,
                    workload=trajectory_contract.workload,
                    segment=SegmentIdentity(
                        segment_id=record.segment_id,
                        version=record.segment_version,
                        attributes=(
                            EvidenceField.from_value(
                                "difficulty", record.difficulty
                            ),
                        ),
                    ),
                    created_at=record.task_created_at,
                ),
                prediction_binding=trajectory_contract.prediction_binding,
                policy_binding=trajectory_contract.policy_binding,
                status="completed",
                started_at=record.trajectory_started_at,
                ended_at=record.timestamp,
                recorded_at=datetime.now(timezone.utc).isoformat(),
                steps=(
                    StepEvidence(
                        step_id=root_step_id,
                        sequence=1,
                        kind=StepKind.ITERATION,
                        status=StepStatus.COMPLETED,
                        operation="task_execution",
                        started_at=record.trajectory_started_at,
                        ended_at=record.timestamp,
                    ),
                    StepEvidence(
                        step_id=child_step_id,
                        sequence=2,
                        kind=child_kind,
                        status=child_status,
                        operation=(
                            "semantic_cache"
                            if record.cache_hit
                            else "model_generation"
                            if record.model != "none"
                            else "budget_rejection"
                        ),
                        started_at=record.trajectory_started_at,
                        ended_at=record.timestamp,
                        parent_step_id=root_step_id,
                        evidence=evidence,
                    ),
                ),
            )
            stored = trajectory_store.append(envelope)
            trajectory_evidence.append({
                "trajectory_id": envelope.trajectory_id,
                "task_id": envelope.task.task_id,
                "segment_id": envelope.task.segment.segment_id,
                "content_hash": stored.content_hash,
            })
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
            "trajectory_contract": trajectory_contract.to_dict(),
            "trajectory_evidence": trajectory_evidence,
            "evaluation_outcomes": [
                asdict(item) for item in quality.outcomes
            ],
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