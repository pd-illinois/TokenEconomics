"""Shared application service for forecast, governed run, and reconciliation."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .acceptance_contracts import (
    ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
    ACCEPTANCE_RULE_SCHEMA_VERSION,
    AcceptanceOutcomeStore,
    AcceptanceRule,
    AcceptanceRuleStore,
    ReviewEvidence,
    ReviewMethod,
    evaluate_acceptance,
)
from .contracts import ExecutionContext, ForecastReceipt, ForecastRequest
from .evaluator import Evaluator
from .gateway import Gateway
from .experiment_contracts import ExperimentManifest
from .meter_ledger import (
    MeterLedgerStore,
    aggregate_meter_entries,
    entries_from_gateway_record,
    reconcile_meter_quantity,
)
from .prediction import PythonPredictorAdapter
from .policy_candidates import (
    PolicyCandidate,
    validate_candidate_application,
    validate_candidate_binding,
)
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

    def plan(
        self,
        run_id: str | None = None,
        *,
        policy_candidate_id: str | None = None,
        policy_candidate_version: str | None = None,
        policy_candidate_content_hash: str | None = None,
    ) -> tuple[ForecastReceipt, PythonPredictorAdapter]:
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
                policy_candidate_id=policy_candidate_id,
                policy_candidate_version=policy_candidate_version,
                policy_candidate_content_hash=policy_candidate_content_hash,
            ))
            forecasts.extend(child.forecasts)
        return ForecastReceipt(
            run_id=run_id,
            workload_version="support-workload-v1",
            golden_set_version="support-golden-v1",
            observation_unit="completed_task",
            forecasts=tuple(forecasts),
            assumptions=assumptions,
            policy_candidate_id=policy_candidate_id,
            policy_candidate_version=policy_candidate_version,
            policy_candidate_content_hash=policy_candidate_content_hash,
        ), adapter

    def run(self, run_id: str, report_id: str, admission: dict) -> dict:
        if admission.get("status") != "admitted" or not admission.get("execution"):
            raise ValueError("an admitted policy binding is required")
        experiment = self._validated_experiment_binding(admission)
        policy_candidate_id = experiment["policy_candidate_id"]
        policy_candidate_version = experiment["policy_candidate_version"]
        policy_candidate_content_hash = experiment["policy_candidate_content_hash"]
        receipt, adapter = self.plan(
            run_id,
            policy_candidate_id=policy_candidate_id,
            policy_candidate_version=policy_candidate_version,
            policy_candidate_content_hash=policy_candidate_content_hash,
        )
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
        if experiment["candidate"] is not None:
            validate_candidate_application(
                experiment["candidate"],
                {
                    "execution.routing_mode": config["routing"]["mode"],
                    "execution.semantic_cache.enabled": config["semantic_cache"][
                        "enabled"
                    ],
                    "execution.context.prune": config["context"]["prune"],
                    "evaluation.sample_rate": config["evaluation"]["sample_rate"],
                    "execution.monetary_budget.per_tenant_usd_per_run": config[
                        "budgets"
                    ]["per_tenant_usd_per_run"],
                },
            )
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
                policy_candidate_id=policy_candidate_id,
                policy_candidate_version=policy_candidate_version,
                policy_candidate_content_hash=policy_candidate_content_hash,
            ))

        evaluator = Evaluator(str(self.root / "golden_set.json"))
        quality = evaluator.continuous(telemetry.sampled)
        reconciliation = ReconciliationService(adapter).reconcile(receipt, telemetry.records)
        outcome_by_task = {item.task_id: item for item in quality.outcomes}
        evaluator_path = Path(__file__).with_name("evaluator.py")
        evaluator_hash = hashlib.sha256(evaluator_path.read_bytes()).hexdigest()
        acceptance_rule_store = AcceptanceRuleStore(
            self.artifacts / receipt.run_id / "acceptance_rules"
        )
        acceptance_outcome_store = AcceptanceOutcomeStore(
            self.artifacts / receipt.run_id / "acceptance_outcomes"
        )
        rules = {}
        for segment in by_segment:
            rule = AcceptanceRule(
                schema_version=ACCEPTANCE_RULE_SCHEMA_VERSION,
                rule_id=f"support-{segment}-acceptance",
                version="acceptance-rules.v1",
                segment_id=segment,
                segment_version=trajectory_contract.segment_schema_version,
                evaluator_id="token-coverage-evaluator",
                evaluator_version="evaluator.v1",
                evaluator_content_hash=evaluator_hash,
                minimum_score=config["evaluation"]["min_quality"],
                created_at="2026-08-31T00:00:00+00:00",
            )
            acceptance_rule_store.append(rule)
            rules[segment] = rule
        trajectory_store = TrajectoryStore(
            self.artifacts / receipt.run_id / "trajectories"
        )
        meter_store = MeterLedgerStore(
            self.artifacts / receipt.run_id / "meter_ledger"
        )
        trajectory_evidence = []
        acceptance_evidence = []
        ledger_records = []
        for record in telemetry.records:
            outcome = outcome_by_task.get(record.task_id)
            automated_review = None
            if outcome is not None:
                raw_outcome = asdict(outcome)
                automated_review = ReviewEvidence(
                    method=ReviewMethod.AUTOMATED,
                    reviewer_id="token-coverage-evaluator",
                    evidence_id=f"evaluation-{record.task_id}",
                    evidence_version="evaluator.v1",
                    evidence_content_hash=hashlib.sha256(
                        json.dumps(
                            raw_outcome,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                    score=outcome.score,
                )
            acceptance = evaluate_acceptance(
                rules[record.segment_id],
                experiment_id=experiment["experiment_id"],
                experiment_revision=experiment["experiment_revision"],
                arm_id=experiment["arm_id"],
                policy_candidate_id=policy_candidate_id,
                policy_candidate_version=policy_candidate_version,
                policy_candidate_content_hash=policy_candidate_content_hash,
                task_id=record.task_id,
                trajectory_id=record.trajectory_id,
                segment_id=record.segment_id,
                segment_version=record.segment_version,
                automated_review=automated_review,
                evaluated_at=record.timestamp,
            )
            stored_acceptance = acceptance_outcome_store.append(acceptance)
            acceptance_evidence.append(
                {
                    "outcome_id": acceptance.outcome_id,
                    "task_id": acceptance.task_id,
                    "trajectory_id": acceptance.trajectory_id,
                    "segment_id": acceptance.segment_id,
                    "decision": acceptance.decision.value,
                    "reason_code": acceptance.reason_code,
                    "content_hash": stored_acceptance.content_hash,
                }
            )
            ledger_entries = entries_from_gateway_record(
                record,
                experiment_id=experiment["experiment_id"],
                experiment_revision=experiment["experiment_revision"],
                arm_id=experiment["arm_id"],
                environment="local_simulation",
                meter_stack_id=experiment["meter_stack_id"],
                meter_stack_version=experiment["meter_stack_version"],
                meter_stack_content_hash=experiment[
                    "meter_stack_content_hash"
                ],
                evaluation_performed=outcome is not None,
            )
            for entry in ledger_entries:
                ledger_records.append(meter_store.append(entry))
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
                EvidenceField.from_value(
                    "acceptance_outcome_id", acceptance.outcome_id
                ),
                EvidenceField.from_value(
                    "acceptance_decision", acceptance.decision.value
                ),
                EvidenceField.from_value(
                    "policy_candidate_id", policy_candidate_id
                ),
                EvidenceField.from_value(
                    "policy_candidate_version", policy_candidate_version
                ),
                EvidenceField.from_value(
                    "policy_candidate_content_hash",
                    policy_candidate_content_hash,
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
                "acceptance_outcome_id": acceptance.outcome_id,
                "meter_entry_ids": [entry.entry_id for entry in ledger_entries],
            })
        ledger_entries = tuple(record.entry for record in ledger_records)
        meter_reconciliation = (
            {
                **reconcile_meter_quantity(
                ledger_entries,
                meter_id="foundry_model",
                source_quantity=sum(
                    record.input_tokens + record.output_tokens
                    for record in telemetry.records
                ),
                tolerance=0.0,
                ),
                "comparison_basis": "derived_gateway_telemetry_self_consistency",
            },
            {
                **reconcile_meter_quantity(
                ledger_entries,
                meter_id="automated_task_evaluation",
                source_quantity=float(len(quality.outcomes)),
                tolerance=0.0,
                ),
                "comparison_basis": "sampled_evaluator_count_self_consistency",
            },
            {
                **reconcile_meter_quantity(
                ledger_entries,
                meter_id="foundry_resources",
                source_quantity=0.0,
                tolerance=0.0,
                ),
                "comparison_basis": "coverage_check_no_source_quantity",
            },
        )
        result = {
            "run_id": receipt.run_id,
            "report_id": report_id,
            "status": "completed",
            "evidence_classification": "simulated",
            "forecast": asdict(receipt),
            "policy": {
                **admission["policy"],
                "handoff_id": admission["handoff_id"],
                "plan_id": admission["plan_id"],
                "receipt_id": admission["receipt_id"],
                "routing_mode": execution["routing_mode"],
                "quality_floor": config["evaluation"]["min_quality"],
                "selection_reason": "execution settings loaded from the admitted policy revision",
                "candidate_id": policy_candidate_id,
                "candidate_version": policy_candidate_version,
                "candidate_content_hash": policy_candidate_content_hash,
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
            "acceptance_contract": {
                "rule_schema_version": ACCEPTANCE_RULE_SCHEMA_VERSION,
                "outcome_schema_version": ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
                "rules": [
                    {
                        "rule_id": rule.rule_id,
                        "version": rule.version,
                        "segment_id": rule.segment_id,
                        "content_hash": rule.content_hash,
                    }
                    for rule in rules.values()
                ],
            },
            "acceptance_outcomes": acceptance_evidence,
            "meter_ledger_evidence": [
                {
                    "entry_id": record.entry.entry_id,
                    "task_id": record.entry.task_id,
                    "trajectory_id": record.entry.trajectory_id,
                    "meter_family": record.entry.meter_family.value,
                    "meter_id": record.entry.meter_id,
                    "content_hash": record.content_hash,
                }
                for record in ledger_records
            ],
            "meter_aggregates": [
                {
                    **asdict(aggregate),
                    "meter_family": aggregate.meter_family.value,
                }
                for aggregate in aggregate_meter_entries(ledger_entries)
            ],
            "meter_reconciliation": list(meter_reconciliation),
        }
        run_dir = self.artifacts / receipt.run_id
        run_dir.mkdir(exist_ok=True)
        (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        telemetry.dump_jsonl(str(run_dir / "telemetry.jsonl"))
        return result

    def _load(self, name: str) -> dict:
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    @staticmethod
    def _validated_experiment_binding(admission: dict) -> dict:
        binding = admission.get("experiment_binding")
        if binding is None:
            policy_binding = admission["trajectory_contract"]["policy_binding"]
            return {
                "experiment_id": "studio-simulation",
                "experiment_revision": "orchestrator.v1",
                "arm_id": "admitted-policy",
                "policy_candidate_id": (
                    f"active-policy-{policy_binding['policy_id']}"
                ),
                "policy_candidate_version": policy_binding["version"],
                "policy_candidate_content_hash": policy_binding["content_hash"],
                "meter_stack_id": "foundry-meter-stack",
                "meter_stack_version": "consumption-models.v1",
                "meter_stack_content_hash": hashlib.sha256(
                    Path(__file__).with_name("consumption_models.py").read_bytes()
                ).hexdigest(),
                "candidate": None,
            }
        try:
            manifest = ExperimentManifest.from_dict(binding["manifest"])
            candidate = PolicyCandidate.from_dict(binding["candidate"])
            arm_id = binding["arm_id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "experiment_binding requires a valid manifest, candidate, and arm_id"
            ) from exc
        validate_candidate_binding(candidate, manifest, arm_id)
        return {
            "experiment_id": manifest.experiment_id,
            "experiment_revision": manifest.revision,
            "arm_id": arm_id,
            "policy_candidate_id": candidate.candidate_id,
            "policy_candidate_version": candidate.version,
            "policy_candidate_content_hash": candidate.content_hash,
            "meter_stack_id": candidate.meter_stack_id,
            "meter_stack_version": candidate.meter_stack_version,
            "meter_stack_content_hash": candidate.meter_stack_content_hash,
            "candidate": candidate,
        }

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