"""Immutable complete-task reconciliation with independent billing evidence."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .acceptance_contracts import AcceptanceDecision, AcceptanceOutcome
from .meter_ledger import CostCoverage, MeterLedgerEntry
from .observe_economics import load_verified_run_evidence
from .trajectory_contracts import TrajectoryEnvelope

RECONCILIATION_EVIDENCE_SCHEMA_VERSION = "reconciliation-evidence.v1"
BILLING_ACTUAL_SCHEMA_VERSION = "billing-actual.v1"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field} must be finite")
    return float(value)


@dataclass(frozen=True)
class BillingActual:
    schema_version: str
    source_export_id: str
    source_file: str
    source_row_hash: str
    billing_period: str
    resource_id: str
    service_name: str
    meter_name: str
    cost: float
    currency: str

    def __post_init__(self) -> None:
        if self.schema_version != BILLING_ACTUAL_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {BILLING_ACTUAL_SCHEMA_VERSION}")
        for field in (
            "source_export_id",
            "source_file",
            "source_row_hash",
            "billing_period",
            "resource_id",
            "service_name",
            "meter_name",
            "currency",
        ):
            _required(getattr(self, field), field)
        if len(self.source_row_hash) != 64:
            raise ValueError("source_row_hash must be a SHA-256 hash")
        if _finite(self.cost, "billing cost") < 0:
            raise ValueError("billing cost must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def load_actual_cost_export(
    path: str | Path,
    *,
    source_export_id: str,
    allowed_resource_ids: Iterable[str],
) -> tuple[BillingActual, ...]:
    """Load immutable ActualCost rows without treating unrelated spend as task cost."""
    export_path = Path(path)
    allowed = {item.lower() for item in allowed_resource_ids}
    opener = gzip.open if export_path.suffix.lower() == ".gz" else open
    with opener(export_path, "rt", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result: list[BillingActual] = []
    for row in rows:
        resource_id = (
            row.get("ResourceId")
            or row.get("resourceId")
            or row.get("ResourceID")
            or ""
        )
        if resource_id.lower() not in allowed:
            continue
        cost_text = row.get("CostInBillingCurrency") or row.get("Cost") or ""
        currency = row.get("BillingCurrencyCode") or row.get("Currency") or ""
        date_text = row.get("Date") or row.get("UsageDate") or ""
        canonical_row = _canonical(row)
        result.append(
            BillingActual(
                schema_version=BILLING_ACTUAL_SCHEMA_VERSION,
                source_export_id=source_export_id,
                source_file=export_path.name,
                source_row_hash=hashlib.sha256(canonical_row.encode()).hexdigest(),
                billing_period=date_text[:7],
                resource_id=resource_id,
                service_name=row.get("ServiceName") or row.get("ConsumedService") or "unknown",
                meter_name=row.get("MeterName") or row.get("MeterCategory") or "unknown",
                cost=float(cost_text),
                currency=currency,
            )
        )
    return tuple(result)


def _nearest_rank(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _segment_reconciliation(
    *,
    segment_id: str,
    trajectories: tuple[TrajectoryEnvelope, ...],
    outcomes: tuple[AcceptanceOutcome, ...],
    entries: tuple[MeterLedgerEntry, ...],
    budget_usd: float,
    forecast_percentiles: Mapping[str, float],
) -> dict[str, Any]:
    task_ids = {
        trajectory.task.task_id
        for trajectory in trajectories
        if trajectory.task.segment.segment_id == segment_id
    }
    segment_outcomes = [item for item in outcomes if item.task_id in task_ids]
    costs: list[float] = []
    incomplete_cost_tasks: list[str] = []
    for task_id in sorted(task_ids):
        task_entries = [item for item in entries if item.task_id == task_id]
        if not task_entries or any(
            item.cost_coverage is CostCoverage.UNPRICED for item in task_entries
        ):
            incomplete_cost_tasks.append(task_id)
            continue
        costs.append(sum(float(item.allocated_cost_usd or 0) for item in task_entries))

    breaches = sum(cost > budget_usd for cost in costs)
    percentile_coverage = {
        name: (
            sum(cost <= float(value) for cost in costs) / len(costs)
            if costs
            else None
        )
        for name, value in sorted(forecast_percentiles.items())
    }
    return {
        "segment_id": segment_id,
        "completed_tasks": len(task_ids),
        "accepted_tasks": sum(
            item.decision is AcceptanceDecision.ACCEPTED for item in segment_outcomes
        ),
        "rejected_tasks": sum(
            item.decision is AcceptanceDecision.REJECTED for item in segment_outcomes
        ),
        "inconclusive_tasks": sum(
            item.decision is AcceptanceDecision.INCONCLUSIVE
            for item in segment_outcomes
        ),
        "priced_task_count": len(costs),
        "incomplete_cost_task_ids": incomplete_cost_tasks,
        "actual_cost_usd": {
            "total": sum(costs) if costs else None,
            "mean_per_completed_task": (
                sum(costs) / len(task_ids) if costs and len(costs) == len(task_ids) else None
            ),
            "mean_per_accepted_task": (
                sum(costs)
                / sum(
                    item.decision is AcceptanceDecision.ACCEPTED
                    for item in segment_outcomes
                )
                if costs
                and len(costs) == len(task_ids)
                and any(
                    item.decision is AcceptanceDecision.ACCEPTED
                    for item in segment_outcomes
                )
                else None
            ),
            "empirical_p50": _nearest_rank(costs, 0.50),
            "empirical_p95": _nearest_rank(costs, 0.95),
            "empirical_p99": _nearest_rank(costs, 0.99),
        },
        "budget": {
            "budget_usd": budget_usd,
            "breach_count": breaches,
            "empirical_breach_rate": breaches / len(costs) if costs else None,
            "denominator": "tasks_with_complete_priced_ledger",
        },
        "forecast_percentile_coverage": percentile_coverage,
    }


def build_reconciliation_evidence(
    *,
    run_result: dict[str, Any],
    run_root: str | Path,
    budget_usd: float,
    forecast_percentiles_by_segment: Mapping[str, Mapping[str, float]],
    billing_actuals: tuple[BillingActual, ...],
    prediction_reference: Mapping[str, str],
    decision_reference: Mapping[str, str],
) -> dict[str, Any]:
    budget = _finite(budget_usd, "budget_usd")
    if budget <= 0:
        raise ValueError("budget_usd must be positive")
    trajectories, outcomes, entries = load_verified_run_evidence(run_result, run_root)
    task_ids = {item.task.task_id for item in trajectories}
    if set(item.task_id for item in outcomes) != task_ids:
        raise ValueError("reconciliation requires exactly one acceptance outcome per task")
    if any(item.task_id not in task_ids for item in entries):
        raise ValueError("meter evidence contains an unknown task")
    for reference_name, reference in (
        ("prediction_reference", prediction_reference),
        ("decision_reference", decision_reference),
    ):
        _required(reference.get("id"), f"{reference_name}.id")
        value = _required(reference.get("content_hash"), f"{reference_name}.content_hash")
        if len(value) != 64:
            raise ValueError(f"{reference_name}.content_hash must be a SHA-256 hash")

    segments = sorted(
        {item.task.segment.segment_id for item in trajectories}
    )
    segment_rows = [
        _segment_reconciliation(
            segment_id=segment,
            trajectories=trajectories,
            outcomes=outcomes,
            entries=entries,
            budget_usd=budget,
            forecast_percentiles=forecast_percentiles_by_segment.get(segment, {}),
        )
        for segment in segments
    ]
    missing: list[str] = []
    if any(row["incomplete_cost_task_ids"] for row in segment_rows):
        missing.append("complete_priced_task_ledger")
    if not billing_actuals:
        missing.append("subscription_actual_cost_export")

    currencies = sorted({item.currency for item in billing_actuals})
    billing_total = (
        sum(item.cost for item in billing_actuals)
        if billing_actuals and len(currencies) == 1
        else None
    )
    evidence = {
        "schema_version": RECONCILIATION_EVIDENCE_SCHEMA_VERSION,
        "reconciliation_id": f"reconciliation-{uuid4().hex}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not missing else "partial",
        "evidence_classification": (
            "measured_with_billing" if not missing else "measured_partial"
        ),
        "run_id": run_result["run_id"],
        "report_id": run_result["report_id"],
        "experiment_id": entries[0].experiment_id if entries else None,
        "experiment_revision": entries[0].experiment_revision if entries else None,
        "prediction_reference": dict(prediction_reference),
        "decision_reference": dict(decision_reference),
        "policy": {
            "candidate_id": entries[0].policy_candidate_id if entries else None,
            "candidate_version": entries[0].policy_candidate_version if entries else None,
            "candidate_content_hash": (
                entries[0].policy_candidate_content_hash if entries else None
            ),
            "active_policy_versions": sorted(
                {item.policy_binding.version for item in trajectories}
            ),
            "active_policy_etags": sorted(
                {item.policy_binding.etag for item in trajectories}
            ),
        },
        "meter_stack": {
            "ids": sorted({item.meter_stack_id for item in entries}),
            "versions": sorted({item.meter_stack_version for item in entries}),
            "content_hashes": sorted(
                {item.meter_stack_content_hash for item in entries}
            ),
        },
        "pricing_revisions": sorted(
            {item.pricing_revision for item in entries if item.pricing_revision}
        ),
        "rate_card_revisions": sorted(
            {item.rate_card_revision for item in entries if item.rate_card_revision}
        ),
        "segments": segment_rows,
        "billing": {
            "source": "azure_cost_management_actual_cost_export",
            "row_count": len(billing_actuals),
            "row_hashes": sorted(item.source_row_hash for item in billing_actuals),
            "currencies": currencies,
            "total_cost": billing_total,
            "claim": (
                "Billing actuals remain independent from task allocation; they do not "
                "replace provider usage or ledger allocation evidence."
            ),
        },
        "missing_evidence": missing,
        "historical_forecast_mutated": False,
    }
    idempotency_payload = {
        key: evidence[key]
        for key in (
            "run_id",
            "report_id",
            "prediction_reference",
            "decision_reference",
            "segments",
            "billing",
        )
    }
    evidence["idempotency_key"] = _hash(idempotency_payload)
    evidence["content_hash"] = _hash(
        {key: value for key, value in evidence.items() if key != "content_hash"}
    )
    return evidence


class ReconciliationEvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(evidence)
        key = _required(payload.get("idempotency_key"), "idempotency_key")
        path = self.root / f"{key}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("content_hash") != payload.get("content_hash"):
                raise ValueError("reconciliation idempotency key collision")
            return existing, False
        temporary = self.root / f".{key}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return payload, True
