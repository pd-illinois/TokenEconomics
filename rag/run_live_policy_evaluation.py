"""Run a resumable 60-easy/60-hard Foundry RAG policy evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from costgov.acceptance_contracts import (
    ACCEPTANCE_RULE_SCHEMA_VERSION,
    AcceptanceOutcomeStore,
    AcceptanceRule,
    ReviewEvidence,
    ReviewMethod,
    evaluate_acceptance,
)
from costgov.commercial_planning import attach_foundry_meter_stack
from costgov.consumption_models import ConsumptionFamily
from costgov.mcp_prediction import McpPredictorClient
from costgov.meter_ledger import (
    METER_LEDGER_SCHEMA_VERSION,
    CostCoverage,
    MeterEvidenceStatus,
    MeterLedgerEntry,
    MeterLedgerStore,
)
from costgov.planning import PlanStore
from costgov.policy_candidates import PolicyCandidate
from costgov.policy_store import load_policy_from_environment
from costgov.reports import ReportStore
from costgov.trajectory_contracts import StepKind, TrajectoryEnvelope, TrajectoryStore
from rag.foundry_trajectory_adapter import (
    FoundryAgentConfig,
    FoundryRagTrajectoryAdapter,
    FoundryTrajectoryRequest,
    create_foundry_openai_client,
)

PROJECT_ENDPOINT = (
    "https://ai-account-xbk6ickycmp22.services.ai.azure.com/api/projects/"
    "ai-project-tokeneconomics-te003"
)
DESCRIPTION = (
    "A Microsoft Foundry prompt agent answers a representative 120-task "
    "five-book RAG workload. Every task retrieves evidence through the pinned "
    "Foundry IQ knowledge base and synthesizes a cited answer with GPT-4.1 Mini."
)
INPUT_USD_PER_MILLION = 0.4
OUTPUT_USD_PER_MILLION = 1.6
SEARCH_BASIC_USD_PER_HOUR = 0.101
PRICING_REVISION = "foundry-model-release.v2:2026-08-25.2"
SEARCH_PRICING_REVISION = "azure-retail-prices:search-basic-eastus:2026-09-01"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _build_tasks(
    golden_set: dict[str, Any], segments: tuple[str, ...] = ("easy", "hard")
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    by_segment = {
        segment: [
            item for item in golden_set["cases"] if item["difficulty"] == segment
        ]
        for segment in segments
    }
    for segment, cases in by_segment.items():
        for index in range(60):
            case = dict(cases[index % len(cases)])
            case["task_index"] = index + 1
            case["segment_id"] = f"rag-{segment}"
            tasks.append(case)
    return tasks


def _step_values(envelope: TrajectoryEnvelope) -> dict[str, Any]:
    model_step = next(step for step in envelope.steps if step.kind is StepKind.MODEL)
    return {
        item.key: json.loads(item.value_json)
        for item in model_step.evidence
    }


def _retrieval_limit(candidate: PolicyCandidate) -> int:
    matches = [
        json.loads(control.value_json)
        for control in candidate.controls
        if control.path == "execution.retrieval.max_output_documents"
    ]
    if (
        len(matches) != 1
        or isinstance(matches[0], bool)
        or not isinstance(matches[0], int)
        or matches[0] < 1
    ):
        raise ValueError(
            "candidate requires one positive integer retrieval "
            "max_output_documents control"
        )
    return matches[0]


def _verify_retrieval_limit(envelope: TrajectoryEnvelope, maximum: int) -> None:
    retrieval_steps = [
        step for step in envelope.steps if step.kind is StepKind.RETRIEVAL
    ]
    if not retrieval_steps:
        raise RuntimeError("trajectory has no retrieval evidence")
    for step in retrieval_steps:
        evidence = {
            item.key: json.loads(item.value_json) for item in step.evidence
        }
        count = evidence.get("retrieved_document_count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise RuntimeError("retrieved document count evidence is unavailable")
        if count > maximum:
            raise RuntimeError(
                f"retrieval returned {count} documents above candidate limit {maximum}"
            )


def _score(answer: str, expected: list[str]) -> tuple[float, dict[str, Any]]:
    normalized = answer.casefold()
    matched = [token for token in expected if token.casefold() in normalized]
    evidence = {
        "evaluator": "explicit_must_include_coverage",
        "expected": expected,
        "matched": matched,
        "score": len(matched) / len(expected),
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
    }
    return float(evidence["score"]), evidence


def _create_plan_and_admission(
    description: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    report_store = ReportStore(ROOT / "studio_reports")
    plan_store = PlanStore(ROOT / "studio_plans")
    report = report_store.create(description)
    parameters = {
        "route": "foundry",
        "model": "gpt-4.1-mini",
        "provider": "azure_openai",
        "users": 120,
        "calls_per_user_per_day": 1,
        "analysis_confirmed": True,
        "confirmed_profile": {
            "agent_pattern": "rag_pipeline",
            "multi_agent_count": 1,
            "modalities": ["text", "document"],
            "tools": ["file_search", "mcp_server", "rag"],
            "document_count": 1,
            "document_pages": 5,
            "searches_per_call": 1,
        },
        "workload_version": "te009-books-rag.v1",
        "segment_schema_version": "segment.v1",
    }
    session = plan_store.create_session(report["report_id"], description, parameters)
    analysis = McpPredictorClient(ROOT).analyze(description)
    result = attach_foundry_meter_stack(
        McpPredictorClient(ROOT).predict(
            description, {**parameters, "analysis": analysis}
        )
    )
    result["infrastructure"] = {
        "status": "estimated",
        "scope": "complete_task_trajectory",
        "search_basic_usd_per_hour": SEARCH_BASIC_USD_PER_HOUR,
        "allocation_method": "experiment wall-clock share divided by completed tasks",
        "pricing_revision": SEARCH_PRICING_REVISION,
        "exclusions": [
            "Invoice actuals are reconciled separately and do not replace this allocation."
        ],
    }
    session, receipt = plan_store.complete(session, result)
    handoff = plan_store.create_govern_handoff(
        session["plan_id"], load_policy_from_environment()
    )
    if handoff["status"] != "admitted":
        failed = [item["name"] for item in handoff["checks"] if not item["passed"]]
        raise RuntimeError(f"live evaluation admission rejected: {', '.join(failed)}")
    report_store.add_artifact(
        report["report_id"],
        "plans",
        {"id": session["plan_id"], "status": session["status"]},
    )
    report_store.add_artifact(
        report["report_id"],
        "receipts",
        {
            "id": receipt["receipt_id"],
            "plan_id": session["plan_id"],
            "content_hash": receipt["content_hash"],
        },
    )
    report_store.add_artifact(
        report["report_id"],
        "govern_handoffs",
        {
            "id": handoff["handoff_id"],
            "status": handoff["status"],
            "policy": handoff["policy"],
        },
    )
    return report, receipt, handoff


def _rule(segment_id: str, evaluator_hash: str) -> AcceptanceRule:
    return AcceptanceRule(
        schema_version=ACCEPTANCE_RULE_SCHEMA_VERSION,
        rule_id=f"explicit-ground-truth-{segment_id}",
        version="2026-09-01.1",
        segment_id=segment_id,
        segment_version="segment.v1",
        evaluator_id="must-include-coverage",
        evaluator_version="2026-09-01.1",
        evaluator_content_hash=evaluator_hash,
        minimum_score=0.8,
        created_at="2026-09-01T00:00:00+00:00",
    )


def _meter_entry(
    *,
    candidate: PolicyCandidate,
    arm_id: str,
    task: dict[str, Any],
    envelope: TrajectoryEnvelope,
    entry_suffix: str,
    meter_family: ConsumptionFamily,
    meter_id: str,
    unit: str,
    quantity: float,
    calculation_method: str,
    allocation_method: str,
    coverage: CostCoverage,
    allocated_cost: float | None,
    pricing_revision: str | None,
    evidence_hash: str,
) -> MeterLedgerEntry:
    return MeterLedgerEntry(
        schema_version=METER_LEDGER_SCHEMA_VERSION,
        entry_id=f"meter-{envelope.task.task_id}-{entry_suffix}",
        experiment_id=candidate.experiment_id,
        experiment_revision=candidate.experiment_revision,
        arm_id=arm_id,
        task_id=envelope.task.task_id,
        trajectory_id=envelope.trajectory_id,
        step_id=None,
        segment_id=task["segment_id"],
        tenant_id="6435fdd8-5f2e-4832-8f52-cc4e715685f6",
        product="microsoft_foundry",
        environment="tokeneconomics-te003",
        meter_stack_id=candidate.meter_stack_id,
        meter_stack_version=candidate.meter_stack_version,
        meter_stack_content_hash=candidate.meter_stack_content_hash,
        policy_candidate_id=candidate.candidate_id,
        policy_candidate_version=candidate.version,
        policy_candidate_content_hash=candidate.content_hash,
        meter_family=meter_family,
        meter_id=meter_id,
        native_unit=unit,
        native_currency="USD",
        quantity=quantity,
        evidence_status=MeterEvidenceStatus.MEASURED,
        entitlement_disposition="pay_as_you_go",
        purchase_source="azure_subscription",
        evidence_source="foundry_responses_and_versioned_allocation",
        evidence_content_hash=evidence_hash,
        pricing_revision=pricing_revision,
        rate_card_revision=None,
        billing_period=envelope.recorded_at[:7],
        calculation_method=calculation_method,
        allocation_method=allocation_method,
        cost_coverage=coverage,
        allocated_cost_usd=allocated_cost,
        recorded_at=envelope.recorded_at,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--agent-name", default="tokengov-books-rag-agent")
    parser.add_argument("--agent-version", default="2")
    parser.add_argument("--arm-id", default="live-baseline")
    parser.add_argument(
        "--candidate",
        default="data/policy_candidates/live-gpt-4-1-mini-topk4.2026-09-01.1.json",
    )
    parser.add_argument(
        "--segments",
        choices=("all", "easy", "hard"),
        default="all",
    )
    parser.add_argument("--policy-label", default="te003-live-v2")
    parser.add_argument("--max-attempts", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    os.environ.update(
        AZURE_APPCONFIG_ENDPOINT="https://appcs-xbk6ickycmp22.azconfig.io",
        TOKENGOV_POLICY_KEY="tokengov:policy",
        TOKENGOV_POLICY_LABEL=args.policy_label,
        TOKENGOV_POLICY_SOURCE="azure",
    )
    golden_path = ROOT / "rag" / "golden_set.rag.json"
    golden = _load_json(golden_path)
    evaluator_hash = hashlib.sha256(golden_path.read_bytes()).hexdigest()
    candidate_path = (ROOT / args.candidate).resolve()
    if ROOT not in candidate_path.parents:
        raise ValueError("--candidate must resolve inside the repository")
    candidate = PolicyCandidate.from_dict(_load_json(candidate_path))
    retrieval_limit = _retrieval_limit(candidate)
    segments = ("easy", "hard") if args.segments == "all" else (args.segments,)
    tasks = _build_tasks(golden, segments)
    description = (
        f"{DESCRIPTION} Candidate {candidate.candidate_id} evaluates "
        f"{', '.join(segments)} material segment evidence."
    )
    run_id = args.run_id or f"run-{uuid4().hex}"
    run_root = ROOT / "studio_runs" / run_id
    completed_path = run_root / "result.json"
    if completed_path.exists():
        completed = _load_json(completed_path)
        print(
            json.dumps(
                {
                    "run_id": completed["run_id"],
                    "report_id": completed["report_id"],
                    "status": "already_completed",
                },
                indent=2,
            )
        )
        return 0
    progress_path = run_root / "live-progress.json"
    if progress_path.exists():
        progress = _load_json(progress_path)
        report = progress["report"]
        receipt = progress["receipt"]
        handoff = progress["handoff"]
    else:
        report, receipt, handoff = _create_plan_and_admission(description)
        progress = {
            "run_id": run_id,
            "report": report,
            "receipt": receipt,
            "handoff": handoff,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "tasks": {},
        }
        _write_json(progress_path, progress)

    trajectory_store = TrajectoryStore(run_root / "trajectories")
    config = FoundryAgentConfig(
        project_endpoint=PROJECT_ENDPOINT,
        agent_name=args.agent_name,
        agent_version=args.agent_version,
    )
    adapter = FoundryRagTrajectoryAdapter(create_foundry_openai_client(config), config)
    for task in tasks:
        key = f"{task['segment_id']}:{task['task_index']:03d}"
        if key in progress["tasks"]:
            continue
        task_id = f"task-{hashlib.sha256(f'{run_id}:{key}'.encode()).hexdigest()[:32]}"
        trajectory_id = f"trajectory-{task_id[5:]}"
        captured = None
        for attempt in range(1, args.max_attempts + 1):
            try:
                captured = adapter.capture(
                    request=FoundryTrajectoryRequest(
                        run_id=run_id,
                        report_id=report["report_id"],
                        task_id=task_id,
                        trajectory_id=trajectory_id,
                        trace_id=hashlib.sha256(trajectory_id.encode()).hexdigest()[:32],
                        segment_id=task["segment_id"],
                        segment_version="segment.v1",
                        question=task["question"],
                        task_created_at=datetime.now(timezone.utc).isoformat(),
                    ),
                    admission=handoff,
                    store=trajectory_store,
                )
                break
            except Exception as exc:
                if attempt == args.max_attempts or "429" not in str(exc):
                    raise
                time.sleep(min(60, 5 * 2 ** (attempt - 1)))
        assert captured is not None
        score, scoring_evidence = _score(
            captured.response_text, list(task["must_include"])
        )
        _verify_retrieval_limit(captured.record.envelope, retrieval_limit)
        progress["tasks"][key] = {
            "case_id": task["id"],
            "task_id": task_id,
            "trajectory_id": trajectory_id,
            "trajectory_hash": captured.record.content_hash,
            "score": score,
            "scoring_evidence": scoring_evidence,
        }
        _write_json(progress_path, progress)

    if "evidence_ended_at" not in progress:
        progress["evidence_ended_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(progress_path, progress)
    ended_at = datetime.fromisoformat(progress["evidence_ended_at"])
    started_at = datetime.fromisoformat(progress["started_at"])
    duration_hours = max((ended_at - started_at).total_seconds(), 1) / 3600
    search_per_task = SEARCH_BASIC_USD_PER_HOUR * duration_hours / len(tasks)
    acceptance_store = AcceptanceOutcomeStore(run_root / "acceptance_outcomes")
    meter_store = MeterLedgerStore(run_root / "meter_ledger")
    trajectory_refs = []
    outcome_refs = []
    meter_refs = []
    for task in tasks:
        key = f"{task['segment_id']}:{task['task_index']:03d}"
        saved = progress["tasks"][key]
        record = trajectory_store.get(saved["trajectory_id"])
        if record is None or record.content_hash != saved["trajectory_hash"]:
            raise RuntimeError("trajectory integrity verification failed")
        trajectory_refs.append(
            {
                "trajectory_id": record.envelope.trajectory_id,
                "content_hash": record.content_hash,
            }
        )
        scoring = saved["scoring_evidence"]
        review = ReviewEvidence(
            method=ReviewMethod.AUTOMATED,
            reviewer_id="must-include-coverage",
            evidence_id=f"evaluation-{saved['task_id']}",
            evidence_version="2026-09-01.1",
            evidence_content_hash=_hash(scoring),
            score=float(saved["score"]),
        )
        rule = _rule(task["segment_id"], evaluator_hash)
        outcome = replace(
            evaluate_acceptance(
                rule,
                experiment_id=candidate.experiment_id,
                experiment_revision=candidate.experiment_revision,
                arm_id=args.arm_id,
                policy_candidate_id=candidate.candidate_id,
                policy_candidate_version=candidate.version,
                policy_candidate_content_hash=candidate.content_hash,
                task_id=saved["task_id"],
                trajectory_id=saved["trajectory_id"],
                segment_id=task["segment_id"],
                segment_version="segment.v1",
                automated_review=review,
                evaluated_at=record.envelope.recorded_at,
            ),
            outcome_id=f"acceptance-{saved['task_id']}",
        )
        outcome_record = acceptance_store.get(outcome.outcome_id)
        if outcome_record is None:
            outcome_record = acceptance_store.append(outcome)
        elif outcome_record.outcome != outcome:
            raise RuntimeError("acceptance outcome integrity verification failed")
        outcome_refs.append(
            {
                "outcome_id": outcome.outcome_id,
                "content_hash": outcome_record.content_hash,
            }
        )
        usage = _step_values(record.envelope)
        token_cost = (
            float(usage["input_tokens"]) * INPUT_USD_PER_MILLION
            + float(usage["output_tokens"]) * OUTPUT_USD_PER_MILLION
        ) / 1_000_000
        common_hash = _hash(
            {
                "trajectory_hash": record.content_hash,
                "usage": {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                },
            }
        )
        entries = (
            _meter_entry(
                candidate=candidate,
                arm_id=args.arm_id,
                task=task,
                envelope=record.envelope,
                entry_suffix="model",
                meter_family=ConsumptionFamily.DIRECT_TOKEN,
                meter_id="gpt-4.1-mini-global-standard",
                unit="model_token",
                quantity=float(usage["total_tokens"]),
                calculation_method=(
                    "input_tokens*0.4/1M + output_tokens*1.6/1M"
                ),
                allocation_method="provider-reported task usage",
                coverage=CostCoverage.PRICED,
                allocated_cost=token_cost,
                pricing_revision=PRICING_REVISION,
                evidence_hash=common_hash,
            ),
            _meter_entry(
                candidate=candidate,
                arm_id=args.arm_id,
                task=task,
                envelope=record.envelope,
                entry_suffix="search",
                meter_family=ConsumptionFamily.RESOURCE,
                meter_id="azure-ai-search-basic-eastus",
                unit="service_hour",
                quantity=duration_hours / len(tasks),
                calculation_method="allocated_service_hours*0.101",
                allocation_method="experiment wall-clock share divided equally by completed tasks",
                coverage=CostCoverage.PRICED,
                allocated_cost=search_per_task,
                pricing_revision=SEARCH_PRICING_REVISION,
                evidence_hash=_hash(
                    {
                        "run_started_at": progress["started_at"],
                        "run_ended_at": ended_at.isoformat(),
                        "completed_tasks": len(tasks),
                    }
                ),
            ),
            _meter_entry(
                candidate=candidate,
                arm_id=args.arm_id,
                task=task,
                envelope=record.envelope,
                entry_suffix="retrieval",
                meter_family=ConsumptionFamily.RETRIEVAL,
                meter_id="foundry-iq-knowledge-base-retrieve",
                unit="retrieval_operation",
                quantity=1,
                calculation_method="count completed knowledge_base_retrieve step",
                allocation_method="cost represented by Search fixed-resource allocation",
                coverage=CostCoverage.NOT_APPLICABLE,
                allocated_cost=None,
                pricing_revision=None,
                evidence_hash=common_hash,
            ),
            _meter_entry(
                candidate=candidate,
                arm_id=args.arm_id,
                task=task,
                envelope=record.envelope,
                entry_suffix="evaluation",
                meter_family=ConsumptionFamily.EVALUATION,
                meter_id="local-explicit-ground-truth-evaluator",
                unit="evaluation",
                quantity=1,
                calculation_method="deterministic must-include token coverage",
                allocation_method="local evaluator has no Azure-billed model invocation",
                coverage=CostCoverage.NOT_APPLICABLE,
                allocated_cost=None,
                pricing_revision=None,
                evidence_hash=_hash(scoring),
            ),
        )
        for entry in entries:
            meter_record = meter_store.get(entry.entry_id)
            if meter_record is None:
                meter_record = meter_store.append(entry)
            elif meter_record.entry != entry:
                raise RuntimeError("meter ledger integrity verification failed")
            meter_refs.append(
                {"entry_id": entry.entry_id, "content_hash": meter_record.content_hash}
            )

    result = {
        "run_id": run_id,
        "report_id": report["report_id"],
        "status": "completed",
        "evidence_classification": "measured_live",
        "experiment_id": candidate.experiment_id,
        "experiment_revision": candidate.experiment_revision,
        "policy_candidate_id": candidate.candidate_id,
        "policy_candidate_version": candidate.version,
        "policy_candidate_content_hash": candidate.content_hash,
        "prediction_receipt_id": receipt["receipt_id"],
        "prediction_receipt_hash": receipt["content_hash"],
        "trajectory_evidence": trajectory_refs,
        "acceptance_outcomes": outcome_refs,
        "meter_ledger_evidence": meter_refs,
        "cost_scope": {
            "included": [
                "provider-reported GPT-4.1 Mini input/output tokens",
                "Azure AI Search Basic experiment wall-clock allocation",
                "retrieval operations without double-counting Search cost",
                "local deterministic ground-truth evaluation",
            ],
            "billing_actuals": "reconciled separately through Cost Management ActualCost export",
            "observability": "no explicit App Insights telemetry emitted by this runner",
        },
        "started_at": progress["started_at"],
        "ended_at": ended_at.isoformat(),
    }
    _write_json(run_root / "result.json", result)
    registry_path = ROOT / "studio_runs" / "registry.json"
    registry = _load_json(registry_path) if registry_path.exists() else {}
    registry[run_id] = {
        "run_id": run_id,
        "report_id": report["report_id"],
        "status": "completed",
        "result": result,
    }
    _write_json(registry_path, registry)
    ReportStore(ROOT / "studio_reports").add_artifact(
        report["report_id"],
        "runs",
        {
            "id": run_id,
            "status": "completed",
            "path": f"studio_runs/{run_id}/result.json",
        },
    )
    print(json.dumps({"run_id": run_id, "report_id": report["report_id"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
