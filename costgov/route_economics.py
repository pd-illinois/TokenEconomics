"""Immutable route-aware complete-task economics over native meter ledgers."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .acceptance_contracts import AcceptanceDecision, AcceptanceOutcome
from .meter_ledger import CostCoverage, MeterEvidenceStatus, MeterLedgerEntry
from .route_capabilities import ROUTE_CAPABILITY_PROFILES

ROUTE_ECONOMICS_SCHEMA_VERSION = "route-task-economics.v1"
_PROFILES = {profile.route_id: profile for profile in ROUTE_CAPABILITY_PROFILES}


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _hash(value: object, field: str) -> str:
    text = _required(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _quantity(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be finite and non-negative")
    return float(value)


class EconomicView(str, Enum):
    FIXED = "fixed_allocated"
    INCREMENTAL = "incremental"


class CapacityKind(str, Enum):
    FIXED_SEAT = "fixed_seat"
    COMMITMENT = "commitment"
    ALLOWANCE = "allowance"


@dataclass(frozen=True)
class CompletedTask:
    task_id: str
    trajectory_id: str
    segment_id: str
    segment_version: str
    completion_evidence_hash: str

    def __post_init__(self) -> None:
        for field in ("task_id", "trajectory_id", "segment_id", "segment_version"):
            _required(getattr(self, field), field)
        _hash(self.completion_evidence_hash, "completion_evidence_hash")

    @classmethod
    def from_value(cls, value: "CompletedTask | Mapping[str, Any]") -> "CompletedTask":
        return value if isinstance(value, cls) else cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class LedgerBinding:
    entry: MeterLedgerEntry
    entry_content_hash: str
    authority: str
    target_leg: str
    economic_view: EconomicView
    applicable: bool = True
    capacity_quantity: float | None = None
    capacity_kind: CapacityKind | None = None
    allocation_revision: str | None = None

    def __post_init__(self) -> None:
        _hash(self.entry_content_hash, "entry_content_hash")
        if _digest(self.entry.to_dict()) != self.entry_content_hash:
            raise ValueError("entry_content_hash does not match the meter ledger entry")
        _required(self.authority, "authority")
        _required(self.target_leg, "target_leg")
        if not isinstance(self.economic_view, EconomicView):
            raise ValueError("economic_view is invalid")
        if (self.capacity_quantity is None) != (self.capacity_kind is None):
            raise ValueError("capacity_quantity and capacity_kind must be supplied together")
        if self.capacity_quantity is not None:
            _quantity(self.capacity_quantity, "capacity_quantity")
            if self.entry.quantity is None:
                raise ValueError("capacity utilization requires a known native quantity")
        if self.capacity_kind is not None and not isinstance(
            self.capacity_kind, CapacityKind
        ):
            raise ValueError("capacity_kind is invalid")
        if self.allocation_revision is not None:
            _required(self.allocation_revision, "allocation_revision")

    @classmethod
    def from_value(cls, value: "LedgerBinding | Mapping[str, Any]") -> "LedgerBinding":
        if isinstance(value, cls):
            return value
        values = dict(value)
        if isinstance(values.get("entry"), Mapping):
            values["entry"] = MeterLedgerEntry.from_dict(values["entry"])
        values["economic_view"] = EconomicView(values.get("economic_view"))
        capacity_kind = values.get("capacity_kind")
        values["capacity_kind"] = (
            CapacityKind(capacity_kind) if capacity_kind is not None else None
        )
        return cls(**values)

    def to_reference(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry.entry_id,
            "entry_content_hash": self.entry_content_hash,
            "authority": self.authority,
            "target_leg": self.target_leg,
            "economic_view": self.economic_view.value,
            "applicable": self.applicable,
            "capacity_quantity": self.capacity_quantity,
            "capacity_kind": (
                self.capacity_kind.value if self.capacity_kind is not None else None
            ),
            "allocation_revision": self.allocation_revision,
        }


def _summary(
    task_ids: set[str],
    outcome_by_task: Mapping[str, AcceptanceOutcome],
    bindings: tuple[LedgerBinding, ...],
) -> dict[str, Any]:
    relevant = tuple(binding for binding in bindings if binding.entry.task_id in task_ids)
    accepted = sum(
        outcome_by_task[task_id].decision is AcceptanceDecision.ACCEPTED
        for task_id in task_ids
    )
    rejected = sum(
        outcome_by_task[task_id].decision is AcceptanceDecision.REJECTED
        for task_id in task_ids
    )
    inconclusive = len(task_ids) - accepted - rejected
    applicable = tuple(binding for binding in relevant if binding.applicable)
    priced = tuple(
        binding
        for binding in applicable
        if binding.entry.cost_coverage is CostCoverage.PRICED
        and binding.entry.evidence_status
        not in {MeterEvidenceStatus.UNAVAILABLE, MeterEvidenceStatus.EXCLUDED}
    )
    unpriced = tuple(
        binding
        for binding in applicable
        if binding.entry.cost_coverage is CostCoverage.UNPRICED
        or binding.entry.evidence_status
        in {MeterEvidenceStatus.UNAVAILABLE, MeterEvidenceStatus.EXCLUDED}
    )
    fixed = sum(
        binding.entry.allocated_cost_usd or 0.0
        for binding in priced
        if binding.economic_view is EconomicView.FIXED
    )
    incremental = sum(
        binding.entry.allocated_cost_usd or 0.0
        for binding in priced
        if binding.economic_view is EconomicView.INCREMENTAL
    )
    priced_total = fixed + incremental
    native_groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for binding in applicable:
        entry = binding.entry
        key = (
            binding.authority,
            binding.target_leg,
            entry.meter_family.value,
            entry.meter_id,
            entry.native_unit,
            entry.native_currency,
            entry.purchase_source,
            binding.economic_view.value,
        )
        row = native_groups.setdefault(
            key,
            {
                "authority": binding.authority,
                "target_leg": binding.target_leg,
                "meter_family": entry.meter_family.value,
                "meter_id": entry.meter_id,
                "native_unit": entry.native_unit,
                "native_currency": entry.native_currency,
                "purchase_source": entry.purchase_source,
                "economic_view": binding.economic_view.value,
                "known_quantity": 0.0,
                "priced_allocated_cost_usd": 0.0,
                "entry_count": 0,
                "unpriced_applicable_entry_ids": [],
                "entry_content_hashes": [],
            },
        )
        row["entry_count"] += 1
        row["known_quantity"] += entry.quantity or 0.0
        row["priced_allocated_cost_usd"] += entry.allocated_cost_usd or 0.0
        row["entry_content_hashes"].append(binding.entry_content_hash)
        if binding in unpriced:
            row["unpriced_applicable_entry_ids"].append(entry.entry_id)

    capacity = []
    for binding in applicable:
        if binding.capacity_quantity is None:
            continue
        used = float(binding.entry.quantity or 0.0)
        capacity.append(
            {
                "entry_id": binding.entry.entry_id,
                "authority": binding.authority,
                "target_leg": binding.target_leg,
                "purchase_source": binding.entry.purchase_source,
                "capacity_kind": binding.capacity_kind.value,
                "native_unit": binding.entry.native_unit,
                "used_quantity": used,
                "capacity_quantity": binding.capacity_quantity,
                "utilization": (
                    used / binding.capacity_quantity
                    if binding.capacity_quantity > 0
                    else None
                ),
                "overage_quantity": max(0.0, used - binding.capacity_quantity),
            }
        )
    return {
        "completed_tasks": len(task_ids),
        "acceptance_denominator": {
            "observation_unit": "completed_task",
            "completed": len(task_ids),
            "accepted": accepted,
            "rejected": rejected,
            "inconclusive": inconclusive,
            "acceptance_probability_estimate": (
                accepted / len(task_ids) if task_ids else None
            ),
            "raw_evaluator_score_substituted": False,
        },
        "monetary_views": {
            "currency": "USD",
            "fixed_allocated": {
                "priced_total_usd": fixed,
                "cost_per_completed_task_usd": (
                    fixed / len(task_ids) if task_ids else None
                ),
                "cost_per_accepted_task_usd": fixed / accepted if accepted else None,
            },
            "incremental": {
                "priced_total_usd": incremental,
                "cost_per_completed_task_usd": (
                    incremental / len(task_ids) if task_ids else None
                ),
                "cost_per_accepted_task_usd": (
                    incremental / accepted if accepted else None
                ),
            },
            "priced_combined": {
                "priced_total_usd": priced_total,
                "cost_per_completed_task_usd": (
                    priced_total / len(task_ids) if task_ids else None
                ),
                "cost_per_accepted_task_usd": (
                    priced_total / accepted if accepted else None
                ),
                "excludes_unpriced_applicable_cost": True,
            },
        },
        "native_ledgers": [
            {
                **row,
                "entry_content_hashes": sorted(row["entry_content_hashes"]),
                "unpriced_applicable_entry_ids": sorted(
                    row["unpriced_applicable_entry_ids"]
                ),
            }
            for _, row in sorted(native_groups.items())
        ],
        "capacity_utilization": capacity,
        "coverage": {
            "applicable_entries": len(applicable),
            "priced_entries": len(priced),
            "unpriced_applicable_entries": len(unpriced),
            "complete": not unpriced,
            "unpriced_applicable_entry_ids": sorted(
                binding.entry.entry_id for binding in unpriced
            ),
        },
    }


def build_route_task_economics(
    *,
    route_id: str,
    completed_tasks: Iterable[CompletedTask | Mapping[str, Any]],
    acceptance_outcomes: Iterable[AcceptanceOutcome | Mapping[str, Any]],
    ledger_bindings: Iterable[LedgerBinding | Mapping[str, Any]],
    allocation_period: str,
    allocation_basis: str,
    allocation_basis_revision: str,
    allocation_basis_content_hash: str,
    evidence_classification: str,
) -> dict[str, Any]:
    """Join complete tasks, explicit outcomes, and distinct native ledgers."""
    try:
        profile = _PROFILES[route_id]
    except KeyError as exc:
        raise ValueError(f"unsupported route: {route_id}") from exc
    tasks = tuple(CompletedTask.from_value(item) for item in completed_tasks)
    outcomes = tuple(
        item if isinstance(item, AcceptanceOutcome) else AcceptanceOutcome.from_dict(item)
        for item in acceptance_outcomes
    )
    bindings = tuple(LedgerBinding.from_value(item) for item in ledger_bindings)
    _required(allocation_period, "allocation_period")
    _required(allocation_basis, "allocation_basis")
    _required(allocation_basis_revision, "allocation_basis_revision")
    _hash(allocation_basis_content_hash, "allocation_basis_content_hash")
    _required(evidence_classification, "evidence_classification")
    if not tasks:
        raise ValueError("complete-task economics requires completed tasks")
    task_by_id = {task.task_id: task for task in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("completed task IDs must be unique")
    outcome_by_task = {outcome.task_id: outcome for outcome in outcomes}
    if len(outcome_by_task) != len(outcomes) or set(outcome_by_task) != set(task_by_id):
        raise ValueError("exactly one acceptance outcome is required per completed task")
    if not bindings or any(binding.entry.task_id not in task_by_id for binding in bindings):
        raise ValueError("ledger bindings must apply only to known completed tasks")
    if {binding.entry.task_id for binding in bindings} != set(task_by_id):
        raise ValueError("every completed task requires applicable ledger evidence")
    periods = {binding.entry.billing_period for binding in bindings}
    if periods != {allocation_period}:
        raise ValueError("all ledger entries must share the common allocation period")
    for outcome in outcomes:
        task = task_by_id[outcome.task_id]
        if (
            outcome.trajectory_id != task.trajectory_id
            or outcome.segment_id != task.segment_id
            or outcome.segment_version != task.segment_version
        ):
            raise ValueError("acceptance outcome does not match completed-task identity")
        task_entries = [
            binding.entry
            for binding in bindings
            if binding.entry.task_id == outcome.task_id
        ]
        if any(
            entry.trajectory_id != task.trajectory_id
            or entry.segment_id != task.segment_id
            or entry.experiment_id != outcome.experiment_id
            or entry.experiment_revision != outcome.experiment_revision
            or entry.arm_id != outcome.arm_id
            or entry.policy_candidate_id != outcome.policy_candidate_id
            or entry.policy_candidate_version != outcome.policy_candidate_version
            or entry.policy_candidate_content_hash
            != outcome.policy_candidate_content_hash
            for entry in task_entries
        ):
            raise ValueError(
                "meter ledger does not match task, experiment, and candidate identity"
            )
    candidate_bindings = {
        (
            binding.entry.policy_candidate_id,
            binding.entry.policy_candidate_version,
            binding.entry.policy_candidate_content_hash,
        )
        for binding in bindings
    }
    meter_bindings = {
        (
            binding.entry.meter_stack_id,
            binding.entry.meter_stack_version,
            binding.entry.meter_stack_content_hash,
        )
        for binding in bindings
    }
    allowed_legs = {control.target_leg for control in profile.controls}
    unknown_legs = {binding.target_leg for binding in bindings} - allowed_legs
    if unknown_legs:
        raise ValueError(f"ledger target leg is not declared by the route: {sorted(unknown_legs)}")

    all_task_ids = set(task_by_id)
    overall = _summary(all_task_ids, outcome_by_task, bindings)
    segment_rows = []
    for segment_id, segment_version in sorted(
        {(task.segment_id, task.segment_version) for task in tasks}
    ):
        selected = {
            task.task_id
            for task in tasks
            if (task.segment_id, task.segment_version) == (segment_id, segment_version)
        }
        segment_rows.append(
            {
                "segment_id": segment_id,
                "segment_version": segment_version,
                **_summary(selected, outcome_by_task, bindings),
            }
        )
    authorities = sorted({binding.authority for binding in bindings})
    target_legs = sorted({binding.target_leg for binding in bindings})
    warnings = []
    if not overall["coverage"]["complete"]:
        warnings.append("partial_priced_cost_coverage")
    expected_legs = {
        "copilot_studio_byom": {"commercial", "foundry"},
        "foundry_work_iq": {"foundry", "work_iq"},
    }.get(route_id)
    if expected_legs and not expected_legs.issubset(target_legs):
        warnings.append("hybrid_leg_ledger_incomplete")
    payload = {
        "schema_version": ROUTE_ECONOMICS_SCHEMA_VERSION,
        "route_id": route_id,
        "capability_profile": {
            "version": profile.version,
            "content_hash": profile.content_hash,
        },
        "allocation": {
            "period": allocation_period,
            "basis": allocation_basis,
            "revision": allocation_basis_revision,
            "content_hash": allocation_basis_content_hash,
        },
        "evidence_classification": evidence_classification,
        "scope": "complete_task_or_trajectory",
        "overall": overall,
        "segments": segment_rows,
        "filters": {
            "route_ids": [route_id],
            "meter_stacks": [
                {
                    "id": item[0],
                    "version": item[1],
                    "content_hash": item[2],
                }
                for item in sorted(meter_bindings)
            ],
            "policy_candidates": [
                {
                    "id": item[0],
                    "version": item[1],
                    "content_hash": item[2],
                }
                for item in sorted(candidate_bindings)
            ],
            "segments": [
                {"id": item["segment_id"], "version": item["segment_version"]}
                for item in segment_rows
            ],
            "authorities": authorities,
            "target_legs": target_legs,
        },
        "evidence": {
            "completed_tasks": [task.to_dict() for task in tasks],
            "acceptance_outcome_hashes": sorted(
                _digest(outcome.to_dict()) for outcome in outcomes
            ),
            "ledger_bindings": [
                binding.to_reference()
                for binding in sorted(bindings, key=lambda item: item.entry.entry_id)
            ],
        },
        "tail_risk": {
            "modeled_percentiles_present": False,
            "calibrated_tail_risk_claim": False,
            "claim": "This projection does not turn modeled percentiles into a calibrated breach guarantee.",
        },
        "warnings": warnings,
        "historical_evidence_mutated": False,
    }
    payload["economics_id"] = f"route-economics-{_digest(payload)[:32]}"
    payload["content_hash"] = _digest(payload)
    return payload
