from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .acceptance_contracts import (
    AcceptanceOutcome,
    AcceptanceOutcomeStore,
    ReviewMethod,
)
from .meter_ledger import MeterLedgerEntry, MeterLedgerStore
from .trajectory_contracts import TrajectoryEnvelope, TrajectoryStore

OBSERVE_ECONOMICS_SCHEMA_VERSION = "accepted-task-economics.v1"


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _rounded(value: float) -> float:
    return round(value, 12)


def _native_meters(entries: Iterable[MeterLedgerEntry]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[MeterLedgerEntry]] = defaultdict(list)
    for entry in entries:
        groups[
            (
                _enum_value(entry.meter_family),
                entry.meter_id,
                entry.native_unit,
                entry.native_currency,
            )
        ].append(entry)

    result = []
    for (family, meter_id, unit, currency), items in sorted(groups.items()):
        known = [float(item.quantity) for item in items if item.quantity is not None]
        priced = [
            float(item.allocated_cost_usd)
            for item in items
            if item.allocated_cost_usd is not None
        ]
        result.append(
            {
                "meter_family": family,
                "meter_id": meter_id,
                "native_unit": unit,
                "native_currency": currency,
                "known_quantity": _rounded(sum(known)) if known else None,
                "entry_count": len(items),
                "unavailable_entry_count": len(items) - len(known),
                "evidence_statuses": sorted(
                    {_enum_value(item.evidence_status) for item in items}
                ),
                "entitlement_dispositions": sorted(
                    {item.entitlement_disposition for item in items}
                ),
                "cost_coverage": sorted(
                    {_enum_value(item.cost_coverage) for item in items}
                ),
                "priced_allocatable_cost_usd": (
                    _rounded(sum(priced)) if priced else None
                ),
            }
        )
    return result


def _uncovered_components(
    entries: Iterable[MeterLedgerEntry],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[MeterLedgerEntry]] = defaultdict(list)
    for entry in entries:
        if _enum_value(entry.cost_coverage) != "unpriced":
            continue
        groups[
            (
                _enum_value(entry.meter_family),
                entry.meter_id,
                entry.native_unit,
                entry.native_currency,
            )
        ].append(entry)
    return [
        {
            "meter_family": key[0],
            "meter_id": key[1],
            "native_unit": key[2],
            "native_currency": key[3],
            "entry_count": len(items),
            "evidence_statuses": sorted(
                {_enum_value(item.evidence_status) for item in items}
            ),
            "reasons": sorted(
                {
                    item.unavailable_reason or "No sourced pricing or allocation evidence."
                    for item in items
                }
            ),
        }
        for key, items in sorted(groups.items())
    ]


def _entitlement_dispositions(
    entries: Iterable[MeterLedgerEntry],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[MeterLedgerEntry]] = defaultdict(list)
    for entry in entries:
        groups[
            (
                entry.entitlement_disposition,
                entry.native_unit,
                entry.native_currency,
            )
        ].append(entry)
    result = []
    for (disposition, unit, currency), items in sorted(groups.items()):
        known = [float(item.quantity) for item in items if item.quantity is not None]
        result.append(
            {
                "disposition": disposition,
                "native_unit": unit,
                "native_currency": currency,
                "known_quantity": _rounded(sum(known)) if known else None,
                "entry_count": len(items),
                "purchase_sources": sorted({item.purchase_source for item in items}),
            }
        )
    return result


def _task_summary(
    task_ids: set[str],
    trajectories: tuple[TrajectoryEnvelope, ...],
    outcomes: tuple[AcceptanceOutcome, ...],
    entries: tuple[MeterLedgerEntry, ...],
) -> dict[str, Any]:
    scoped_trajectories = [
        item for item in trajectories if item.task.task_id in task_ids
    ]
    scoped_outcomes = [item for item in outcomes if item.task_id in task_ids]
    scoped_entries = [item for item in entries if item.task_id in task_ids]

    attempted = len(scoped_trajectories)
    completed = sum(item.status == "completed" for item in scoped_trajectories)
    decisions = {"accepted": 0, "rejected": 0, "inconclusive": 0}
    for outcome in scoped_outcomes:
        decisions[_enum_value(outcome.decision)] += 1
    evaluated = len(scoped_outcomes)
    scores = [
        float(review.score)
        for outcome in scoped_outcomes
        for review in outcome.reviews
        if (
            _enum_value(review.method) == ReviewMethod.AUTOMATED.value
            and review.score is not None
        )
    ]
    priced = [
        float(item.allocated_cost_usd)
        for item in scoped_entries
        if item.allocated_cost_usd is not None
    ]
    priced_total = _rounded(sum(priced)) if priced else None
    unpriced_count = sum(
        _enum_value(item.cost_coverage) == "unpriced" for item in scoped_entries
    )
    not_applicable_count = sum(
        _enum_value(item.cost_coverage) == "not_applicable"
        for item in scoped_entries
    )
    coverage_status = (
        "unavailable"
        if not scoped_entries
        else "incomplete"
        if unpriced_count
        else "complete"
    )
    return {
        "operational_completion": {
            "attempted_tasks": attempted,
            "completed_tasks": completed,
            "incomplete_tasks": attempted - completed,
            "completion_rate": _ratio(completed, attempted),
            "denominator": "persisted_trajectory_count",
        },
        "acceptance": {
            "evaluated_tasks": evaluated,
            **decisions,
            "acceptance_rate": _ratio(decisions["accepted"], evaluated),
            "denominator": "explicit_acceptance_outcome_count",
        },
        "quality": {
            "scored_tasks": len(scores),
            "mean_automated_score": (
                _rounded(sum(scores) / len(scores)) if scores else None
            ),
            "claim": "Evaluator scores are evidence inputs, not acceptance probabilities.",
        },
        "economics": {
            "priced_allocatable_cost_usd": priced_total,
            "coverage_status": coverage_status,
            "priced_entry_count": len(priced),
            "unpriced_entry_count": unpriced_count,
            "not_applicable_entry_count": not_applicable_count,
            "ledger_entry_count": len(scoped_entries),
            "priced_cost_per_completed_task_usd": (
                _rounded(priced_total / completed)
                if priced_total is not None and completed
                else None
            ),
            "priced_cost_per_accepted_task_usd": (
                _rounded(priced_total / decisions["accepted"])
                if priced_total is not None and decisions["accepted"]
                else None
            ),
            "uncovered_components": _uncovered_components(scoped_entries),
        },
        "native_meters": _native_meters(scoped_entries),
        "entitlement_dispositions": _entitlement_dispositions(scoped_entries),
    }


def _dimension_rows(
    entries: tuple[MeterLedgerEntry, ...],
    trajectories: tuple[TrajectoryEnvelope, ...],
    outcomes: tuple[AcceptanceOutcome, ...],
    *,
    dimension: str,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], set[str]] = defaultdict(set)
    metadata: dict[tuple[str, ...], dict[str, str]] = {}

    if dimension == "segment":
        for item in trajectories:
            key = (item.task.segment.segment_id, item.task.segment.version)
            groups[key].add(item.task.task_id)
            metadata[key] = {"segment_id": key[0], "segment_version": key[1]}
    else:
        for entry in entries:
            if dimension == "product":
                key = (entry.product,)
                values = {"product": entry.product}
            elif dimension == "environment":
                key = (entry.environment,)
                values = {"environment": entry.environment}
            elif dimension == "meter_stack":
                key = (entry.meter_stack_id, entry.meter_stack_version)
                values = {
                    "meter_stack_id": entry.meter_stack_id,
                    "meter_stack_version": entry.meter_stack_version,
                    "meter_stack_content_hash": entry.meter_stack_content_hash,
                }
            elif dimension == "policy_candidate":
                key = (
                    entry.policy_candidate_id,
                    entry.policy_candidate_version,
                )
                values = {
                    "policy_candidate_id": entry.policy_candidate_id,
                    "policy_candidate_version": entry.policy_candidate_version,
                    "policy_candidate_content_hash": (
                        entry.policy_candidate_content_hash
                    ),
                }
            else:
                raise ValueError(f"unsupported Observe dimension: {dimension}")
            groups[key].add(entry.task_id)
            metadata[key] = values

    return [
        {
            **metadata[key],
            **_task_summary(task_ids, trajectories, outcomes, entries),
        }
        for key, task_ids in sorted(groups.items())
    ]


def build_observe_economics(
    *,
    run_id: str,
    report_id: str,
    evidence_classification: str,
    trajectories: tuple[TrajectoryEnvelope, ...],
    outcomes: tuple[AcceptanceOutcome, ...],
    entries: tuple[MeterLedgerEntry, ...],
    integrity_verified: bool = False,
) -> dict[str, Any]:
    task_ids = {item.task.task_id for item in trajectories}
    if len(task_ids) != len(trajectories):
        raise ValueError("Observe requires one trajectory per task")
    if len({item.task_id for item in outcomes}) != len(outcomes):
        raise ValueError("Observe requires at most one acceptance outcome per task")
    if any(item.run_id != run_id for item in trajectories):
        raise ValueError("trajectory run binding does not match Observe run")
    if any(item.task.report_id != report_id for item in trajectories):
        raise ValueError("trajectory report binding does not match Observe report")
    if any(item.task_id not in task_ids for item in outcomes):
        raise ValueError("acceptance outcome is not bound to a persisted trajectory")
    if any(item.task_id not in task_ids for item in entries):
        raise ValueError("meter entry is not bound to a persisted trajectory")

    projection = {
        "schema_version": OBSERVE_ECONOMICS_SCHEMA_VERSION,
        "run_id": run_id,
        "report_id": report_id,
        "evidence_classification": evidence_classification,
        "evidence_basis": {
            "trajectory_records": len(trajectories),
            "acceptance_outcome_records": len(outcomes),
            "meter_ledger_records": len(entries),
            "integrity_verified": integrity_verified,
            "claim": (
                "Read-only complete-task projection over verified immutable records."
                if integrity_verified
                else "Read-only in-memory projection; store integrity was not verified."
            ),
        },
        "overall": _task_summary(task_ids, trajectories, outcomes, entries),
        "dimensions": {
            "segments": _dimension_rows(
                entries, trajectories, outcomes, dimension="segment"
            ),
            "products": _dimension_rows(
                entries, trajectories, outcomes, dimension="product"
            ),
            "environments": _dimension_rows(
                entries, trajectories, outcomes, dimension="environment"
            ),
            "meter_stacks": _dimension_rows(
                entries, trajectories, outcomes, dimension="meter_stack"
            ),
            "policy_candidates": _dimension_rows(
                entries, trajectories, outcomes, dimension="policy_candidate"
            ),
        },
        "committed_capacity": {
            "status": "unavailable",
            "utilization": None,
            "reason": (
                "No committed-capacity denominator is present in this run's "
                "meter-ledger evidence."
            ),
        },
        "external_operational_evidence": {
            "agent365": {
                "status": "not_integrated",
                "included_in_acceptance": False,
                "included_in_economics": False,
                "claim": (
                    "Identity, activity, security, compliance, and adoption "
                    "signals remain separate and unavailable for this workload."
                ),
            }
        },
    }
    canonical = json.dumps(
        projection, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    projection["projection_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    return projection


def _load_verified(
    refs: list[dict[str, Any]],
    *,
    id_field: str,
    store: object,
    record_field: str,
) -> tuple[object, ...]:
    loaded = []
    seen: set[str] = set()
    for reference in refs:
        evidence_id = str(reference.get(id_field, "")).strip()
        if not evidence_id or evidence_id in seen:
            raise ValueError(f"invalid or duplicate {id_field} in Observe evidence")
        seen.add(evidence_id)
        record = store.get(evidence_id)
        if record is None or record.content_hash != reference.get("content_hash"):
            raise ValueError(f"{id_field} integrity check failed")
        loaded.append(getattr(record, record_field))
    return tuple(loaded)


def load_observe_economics(
    run_result: dict[str, Any],
    run_root: str | Path,
) -> dict[str, Any]:
    trajectories, outcomes, entries = load_verified_run_evidence(
        run_result, run_root
    )
    return build_observe_economics(
        run_id=str(run_result["run_id"]),
        report_id=str(run_result["report_id"]),
        evidence_classification=str(
            run_result.get("evidence_classification") or "unknown"
        ),
        trajectories=trajectories,
        outcomes=outcomes,
        entries=entries,
        integrity_verified=True,
    )


def load_verified_run_evidence(
    run_result: dict[str, Any],
    run_root: str | Path,
) -> tuple[
    tuple[TrajectoryEnvelope, ...],
    tuple[AcceptanceOutcome, ...],
    tuple[MeterLedgerEntry, ...],
]:
    if run_result.get("status") != "completed":
        raise ValueError("Observe requires a completed run")
    run_id = str(run_result.get("run_id", "")).strip()
    report_id = str(run_result.get("report_id", "")).strip()
    if not run_id or not report_id:
        raise ValueError("Observe requires run and report identities")
    root = Path(run_root)
    trajectories = _load_verified(
        list(run_result.get("trajectory_evidence") or []),
        id_field="trajectory_id",
        store=TrajectoryStore(root / "trajectories"),
        record_field="envelope",
    )
    outcomes = _load_verified(
        list(run_result.get("acceptance_outcomes") or []),
        id_field="outcome_id",
        store=AcceptanceOutcomeStore(root / "acceptance_outcomes"),
        record_field="outcome",
    )
    entries = _load_verified(
        list(run_result.get("meter_ledger_evidence") or []),
        id_field="entry_id",
        store=MeterLedgerStore(root / "meter_ledger"),
        record_field="entry",
    )
    return trajectories, outcomes, entries
