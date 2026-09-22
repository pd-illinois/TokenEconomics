"""Workload adapter for a privacy-minimized Work IQ portability proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from costgov.acceptance_contracts import (
    ACCEPTANCE_RULE_SCHEMA_VERSION,
    AcceptanceOutcomeStore,
    AcceptanceRule,
    AcceptanceRuleStore,
    ReviewEvidence,
    ReviewMethod,
    evaluate_acceptance,
)
from costgov.consumption_models import ConsumptionFamily
from costgov.meter_ledger import (
    METER_LEDGER_SCHEMA_VERSION,
    CostCoverage,
    MeterEvidenceStatus,
    MeterLedgerEntry,
    MeterLedgerStore,
)
from costgov.portability_evidence import (
    PortabilityEvidenceStore,
    build_portability_proof,
)
from costgov.reports import ReportStore
from costgov.trajectory_contracts import (
    TRAJECTORY_SCHEMA_VERSION,
    EvidenceField,
    PolicyBinding,
    PredictionBinding,
    SegmentIdentity,
    StepEvidence,
    StepKind,
    StepStatus,
    TaskIdentity,
    TrajectoryEnvelope,
    TrajectoryStore,
    WorkloadIdentity,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workiq-count", type=int, required=True)
    parser.add_argument("--calendar-count", type=int, required=True)
    parser.add_argument("--calendar-total", type=int, required=True)
    parser.add_argument("--calendar-cancelled", type=int, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--time-zone", required=True)
    return parser.parse_args()


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _append_or_get(store, value, *identity: str):
    try:
        return store.append(value)
    except FileExistsError:
        existing = store.get(*identity)
        if existing is None:
            raise
        return existing


def main() -> int:
    args = _parse_args()
    if min(
        args.workiq_count,
        args.calendar_count,
        args.calendar_total,
        args.calendar_cancelled,
    ) < 0:
        raise ValueError("event counts must be non-negative")
    if args.calendar_total - args.calendar_cancelled != args.calendar_count:
        raise ValueError("calendar count must exclude cancelled events")

    now = datetime.now(timezone.utc).isoformat()
    run_id = f"te016-workiq-calendar-{args.date.replace('-', '')}"
    existing_result_path = ROOT / "studio_runs" / run_id / "result.json"
    if existing_result_path.exists():
        result = json.loads(existing_result_path.read_text(encoding="utf-8"))
        reference_fields = (
            ("trajectory_evidence", "trajectory_id"),
            ("acceptance_outcomes", "outcome_id"),
            ("meter_ledger_evidence", "entry_id"),
        )
        migrated = False
        for collection, id_field in reference_fields:
            for reference in result.get(collection, []):
                if "id" in reference:
                    reference[id_field] = reference.pop("id")
                    migrated = True
        if migrated:
            existing_result_path.write_text(
                json.dumps(result, indent=2, allow_nan=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        existing = next(
            (
                item
                for item in PortabilityEvidenceStore(
                    ROOT / "studio_portability_evidence"
                ).list()
                if item["run_id"] == run_id
            ),
            None,
        )
        if existing is None:
            raise ValueError("portability run exists without its immutable proof")
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "report_id": existing["report_id"],
                    "portability_proof_id": existing["portability_proof_id"],
                    "content_hash": existing["content_hash"],
                    "created": False,
                    "accepted": existing["accepted_task_count"] == 1,
                    "persisted_private_content": False,
                    "contract_amendments_required": [],
                },
                indent=2,
            )
        )
        return 0
    experiment_id = "workiq-calendar-portability"
    experiment_revision = "2026-09-01.1"
    workload = WorkloadIdentity(
        workload_id="m365-calendar-count",
        version="workiq-calendar-count.v1",
    )
    segment = SegmentIdentity(
        segment_id="calendar-count",
        version="segment.v1",
        attributes=(
            EvidenceField.from_value("experience", "work_iq"),
            EvidenceField.from_value("privacy_scope", "normalized_counts_only"),
        ),
    )
    report_store = ReportStore(ROOT / "studio_reports")
    report = report_store.create("Work IQ calendar-count portability proof")
    report_id = report["report_id"]

    workiq_evidence = {
        "source": "Microsoft Work IQ MCP",
        "date": args.date,
        "normalized_active_event_count": args.workiq_count,
        "response_content_persisted": False,
    }
    calendar_evidence = {
        "source": "Microsoft Graph calendarView",
        "date": args.date,
        "time_zone": args.time_zone,
        "total_rows": args.calendar_total,
        "cancelled_rows": args.calendar_cancelled,
        "normalized_active_event_count": args.calendar_count,
        "event_identifiers_persisted": False,
    }
    prediction_receipt = {
        "schema_version": "portability-forecast.v1",
        "prediction_id": "workiq-calendar-forecast-001",
        "scope": "complete_task",
        "modeled_operations": {
            "workiq_query": 1,
            "graph_validation": 1,
        },
        "commercial_cost": None,
        "copilot_credit_quantity": None,
        "evidence_classification": "modeled_partial",
    }
    prediction_hash = _hash(prediction_receipt)
    prediction_path = ROOT / "studio_runs" / run_id / "prediction-receipt.json"
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_path.write_text(
        json.dumps(
            {**prediction_receipt, "content_hash": prediction_hash},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    candidate = {
        "candidate_id": "workiq-observation-only",
        "version": "2026-09-01.1",
        "control_scope": "correlation_only",
        "external_runtime_authority": False,
    }
    candidate_hash = _hash(candidate)
    task_id = f"task-{_hash(workiq_evidence)[:32]}"
    trajectory_id = f"trajectory-{_hash(calendar_evidence)[:32]}"
    task = TaskIdentity(
        task_id=task_id,
        report_id=report_id,
        workload=workload,
        segment=segment,
        created_at=now,
    )
    steps = (
        StepEvidence(
            step_id="step-workiq-count",
            sequence=1,
            kind=StepKind.TOOL,
            status=StepStatus.COMPLETED,
            operation="workiq_calendar_count",
            started_at=now,
            ended_at=now,
            evidence=(
                EvidenceField.from_value("normalized_count", args.workiq_count),
                EvidenceField.from_value("evidence_content_hash", _hash(workiq_evidence)),
                EvidenceField.from_value("raw_content_persisted", False),
            ),
        ),
        StepEvidence(
            step_id="step-graph-ground-truth",
            sequence=2,
            kind=StepKind.TOOL,
            status=StepStatus.COMPLETED,
            operation="graph_calendar_view_count",
            started_at=now,
            ended_at=now,
            evidence=(
                EvidenceField.from_value("normalized_count", args.calendar_count),
                EvidenceField.from_value("evidence_content_hash", _hash(calendar_evidence)),
                EvidenceField.from_value("raw_content_persisted", False),
            ),
        ),
    )
    envelope = TrajectoryEnvelope(
        schema_version=TRAJECTORY_SCHEMA_VERSION,
        trajectory_id=trajectory_id,
        run_id=run_id,
        trace_id=uuid4().hex,
        task=task,
        prediction_binding=PredictionBinding(
            prediction_id=prediction_receipt["prediction_id"],
            receipt_id="workiq-calendar-prediction-receipt",
            schema_version=prediction_receipt["schema_version"],
            content_hash=prediction_hash,
        ),
        policy_binding=PolicyBinding(
            policy_id="tokengov-te003-live-proof",
            version="2026-08-31.1",
            content_hash="ab1d22c2c1271ac3f87cace0a85671c0ec1ee077bea2f169a0943cb19c879594",
            source="azure_app_configuration",
            label="te003-live-v2",
            etag="cvO1KGul1sC2Mfpk0wZ0u-CukrL1uXRFczkqujEZPCk",
        ),
        status="completed",
        started_at=now,
        ended_at=now,
        recorded_at=now,
        steps=steps,
    )
    run_root = ROOT / "studio_runs" / run_id
    trajectory_record = _append_or_get(
        TrajectoryStore(run_root / "trajectories"), envelope, trajectory_id
    )

    evaluator_snapshot = {
        "method": "exact normalized count equality",
        "workiq_count": args.workiq_count,
        "calendar_count": args.calendar_count,
    }
    rule = AcceptanceRule(
        schema_version=ACCEPTANCE_RULE_SCHEMA_VERSION,
        rule_id="calendar-count-exact-match",
        version="calendar-count-evaluator.v1",
        segment_id=segment.segment_id,
        segment_version=segment.version,
        evaluator_id="exact-count-evaluator",
        evaluator_version="calendar-count-evaluator.v1",
        evaluator_content_hash=_hash({"method": "exact normalized count equality"}),
        minimum_score=1.0,
        created_at=now,
    )
    rule_record = _append_or_get(
        AcceptanceRuleStore(run_root / "acceptance_rules"),
        rule,
        rule.rule_id,
        rule.version,
    )
    score = 1.0 if args.workiq_count == args.calendar_count else 0.0
    outcome = evaluate_acceptance(
        rule,
        experiment_id=experiment_id,
        experiment_revision=experiment_revision,
        arm_id="workiq-live",
        policy_candidate_id=candidate["candidate_id"],
        policy_candidate_version=candidate["version"],
        policy_candidate_content_hash=candidate_hash,
        task_id=task_id,
        trajectory_id=trajectory_id,
        segment_id=segment.segment_id,
        segment_version=segment.version,
        automated_review=ReviewEvidence(
            method=ReviewMethod.AUTOMATED,
            reviewer_id="exact-count-evaluator",
            evidence_id="workiq-graph-count-comparison",
            evidence_version="calendar-count-evaluator.v1",
            evidence_content_hash=_hash(evaluator_snapshot),
            score=score,
        ),
        evaluated_at=now,
    )
    outcome_record = _append_or_get(
        AcceptanceOutcomeStore(run_root / "acceptance_outcomes"),
        outcome,
        outcome.outcome_id,
    )

    meter_stack = {
        "meter_stack_id": "workiq-graph-hybrid",
        "version": "consumption-models.v1",
        "meters": [
            "workiq_operation",
            "graph_validation_operation",
            "microsoft_copilot_credit",
            "m365_entitlement",
        ],
    }
    meter_stack_hash = _hash(meter_stack)
    meter_specs = [
        {
            "entry_id": "meter-workiq-operation",
            "family": ConsumptionFamily.TOOL,
            "meter_id": "workiq_operation",
            "unit": "operation",
            "currency": "workiq_operation",
            "quantity": 1.0,
            "status": MeterEvidenceStatus.MEASURED,
            "source": "Microsoft Work IQ MCP",
            "source_hash": _hash(workiq_evidence),
            "coverage": CostCoverage.UNPRICED,
            "reason": None,
        },
        {
            "entry_id": "meter-graph-validation",
            "family": ConsumptionFamily.EVALUATION,
            "meter_id": "graph_validation_operation",
            "unit": "operation",
            "currency": "graph_operation",
            "quantity": 1.0,
            "status": MeterEvidenceStatus.MEASURED,
            "source": "Microsoft Graph calendarView",
            "source_hash": _hash(calendar_evidence),
            "coverage": CostCoverage.UNPRICED,
            "reason": None,
        },
        {
            "entry_id": "meter-copilot-credits",
            "family": ConsumptionFamily.NATIVE_CREDIT,
            "meter_id": "microsoft_copilot_credit",
            "unit": "Microsoft Copilot Credit",
            "currency": "Microsoft Copilot Credits",
            "quantity": None,
            "status": MeterEvidenceStatus.UNAVAILABLE,
            "source": "Work IQ response",
            "source_hash": _hash({"source": "Work IQ response", "meter": "not_exposed"}),
            "coverage": CostCoverage.UNPRICED,
            "reason": "Work IQ did not expose Copilot Credit consumption.",
        },
        {
            "entry_id": "meter-m365-entitlement",
            "family": ConsumptionFamily.SUBSCRIPTION,
            "meter_id": "m365_entitlement",
            "unit": "license",
            "currency": "entitlement",
            "quantity": None,
            "status": MeterEvidenceStatus.UNAVAILABLE,
            "source": "tenant licensing",
            "source_hash": _hash({"source": "tenant licensing", "status": "not_queried"}),
            "coverage": CostCoverage.UNPRICED,
            "reason": "Authoritative tenant license evidence was not queried.",
        },
    ]
    ledger_store = MeterLedgerStore(run_root / "meter_ledger")
    meter_records = []
    for spec in meter_specs:
        entry = MeterLedgerEntry(
            schema_version=METER_LEDGER_SCHEMA_VERSION,
            entry_id=spec["entry_id"],
            experiment_id=experiment_id,
            experiment_revision=experiment_revision,
            arm_id="workiq-live",
            task_id=task_id,
            trajectory_id=trajectory_id,
            step_id=None,
            segment_id=segment.segment_id,
            tenant_id="tenant-redacted",
            product="microsoft_work_iq",
            environment="m365-tenant",
            meter_stack_id=meter_stack["meter_stack_id"],
            meter_stack_version=meter_stack["version"],
            meter_stack_content_hash=meter_stack_hash,
            policy_candidate_id=candidate["candidate_id"],
            policy_candidate_version=candidate["version"],
            policy_candidate_content_hash=candidate_hash,
            meter_family=spec["family"],
            meter_id=spec["meter_id"],
            native_unit=spec["unit"],
            native_currency=spec["currency"],
            quantity=spec["quantity"],
            evidence_status=spec["status"],
            entitlement_disposition="unavailable",
            purchase_source="unavailable",
            evidence_source=spec["source"],
            evidence_content_hash=spec["source_hash"],
            pricing_revision=None,
            rate_card_revision=None,
            billing_period=args.date[:7],
            calculation_method="provider_reported_operation_or_unavailable",
            allocation_method="direct_to_complete_task",
            cost_coverage=spec["coverage"],
            allocated_cost_usd=None,
            recorded_at=now,
            unavailable_reason=spec["reason"],
        )
        meter_records.append(_append_or_get(ledger_store, entry, entry.entry_id))

    result = {
        "run_id": run_id,
        "report_id": report_id,
        "status": "completed",
        "evidence_classification": "measured_live",
        "experiment_id": experiment_id,
        "experiment_revision": experiment_revision,
        "policy_candidate_id": candidate["candidate_id"],
        "policy_candidate_version": candidate["version"],
        "policy_candidate_content_hash": candidate_hash,
        "prediction_receipt_id": "workiq-calendar-prediction-receipt",
        "prediction_receipt_hash": prediction_hash,
        "trajectory_evidence": [
            {
                "trajectory_id": trajectory_id,
                "content_hash": trajectory_record.content_hash,
            }
        ],
        "acceptance_outcomes": [
            {
                "outcome_id": outcome.outcome_id,
                "content_hash": outcome_record.content_hash,
            }
        ],
        "meter_ledger_evidence": [
            {
                "entry_id": record.entry.entry_id,
                "content_hash": record.content_hash,
            }
            for record in meter_records
        ],
        "cost_scope": {
            "scope": "complete_task",
            "status": "partial",
            "unavailable": ["copilot_credit_quantity", "license_entitlement", "USD_cost"],
        },
        "started_at": now,
        "ended_at": now,
    }
    result_path = run_root / "result.json"
    result_path.write_text(
        json.dumps(result, indent=2, allow_nan=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    proof = build_portability_proof(
        run_id=run_id,
        report_id=report_id,
        workload_id=workload.workload_id,
        workload_version=workload.version,
        trajectory_reference={
            "id": trajectory_id,
            "content_hash": trajectory_record.content_hash,
        },
        acceptance_reference={
            "id": outcome.outcome_id,
            "content_hash": outcome_record.content_hash,
        },
        meter_references=[
            {"id": record.entry.entry_id, "content_hash": record.content_hash}
            for record in meter_records
        ],
        observed_task_count=1,
        accepted_task_count=1 if score == 1 else 0,
        meter_summary=[
            {
                "meter_id": spec["meter_id"],
                "native_currency": spec["currency"],
                "quantity": spec["quantity"],
                "evidence_status": spec["status"].value,
            }
            for spec in meter_specs
        ],
    )
    stored, created = PortabilityEvidenceStore(
        ROOT / "studio_portability_evidence"
    ).append(proof)
    report_store.add_artifact(
        report_id,
        "runs",
        {"id": run_id, "status": "completed", "path": f"studio_runs/{run_id}/result.json"},
    )
    report_store.add_artifact(
        report_id,
        "portability_proofs",
        {
            "id": stored["portability_proof_id"],
            "content_hash": stored["content_hash"],
            "accepted_task_count": stored["accepted_task_count"],
        },
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "report_id": report_id,
                "portability_proof_id": stored["portability_proof_id"],
                "content_hash": stored["content_hash"],
                "created": created,
                "accepted": score == 1,
                "persisted_private_content": False,
                "contract_amendments_required": [],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
