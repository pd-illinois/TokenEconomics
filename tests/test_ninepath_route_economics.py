from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from costgov.acceptance_contracts import (
    ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
    AcceptanceDecision,
    AcceptanceOutcome,
)
from costgov.consumption_models import ConsumptionFamily
from costgov.meter_ledger import (
    METER_LEDGER_SCHEMA_VERSION,
    CostCoverage,
    MeterEvidenceStatus,
    MeterLedgerEntry,
)
from costgov.route_capabilities import ROUTE_CAPABILITY_PROFILES
from costgov.route_economics import (
    CapacityKind,
    EconomicView,
    LedgerBinding,
    build_route_task_economics,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTES = [profile.route_id for profile in ROUTE_CAPABILITY_PROFILES]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _outcome(route: str, decision: AcceptanceDecision) -> AcceptanceOutcome:
    return AcceptanceOutcome(
        schema_version=ACCEPTANCE_OUTCOME_SCHEMA_VERSION,
        outcome_id=f"outcome-{route}",
        experiment_id="experiment",
        experiment_revision="v1",
        arm_id="candidate",
        policy_candidate_id="candidate",
        policy_candidate_version="v1",
        policy_candidate_content_hash="b" * 64,
        task_id=f"task-{route}",
        trajectory_id=f"trajectory-{route}",
        segment_id="hard",
        segment_version="v1",
        rule_id="rule",
        rule_version="v1",
        rule_content_hash="a" * 64,
        decision=decision,
        reason_code="explicit_outcome",
        evaluated_at="2026-09-02T12:00:00+00:00",
        reviews=(),
    )


def _entry(
    route: str,
    index: int,
    *,
    family: ConsumptionFamily = ConsumptionFamily.SUBSCRIPTION,
    coverage: CostCoverage = CostCoverage.PRICED,
    cost: float | None = 10.0,
) -> MeterLedgerEntry:
    return MeterLedgerEntry(
        schema_version=METER_LEDGER_SCHEMA_VERSION,
        entry_id=f"entry-{route}-{index}",
        experiment_id="experiment",
        experiment_revision="v1",
        arm_id="candidate",
        task_id=f"task-{route}",
        trajectory_id=f"trajectory-{route}",
        step_id=None,
        segment_id="hard",
        tenant_id="tenant",
        product=route,
        environment="test",
        meter_stack_id=route,
        meter_stack_version="consumption-models.v1",
        meter_stack_content_hash="c" * 64,
        policy_candidate_id="candidate",
        policy_candidate_version="v1",
        policy_candidate_content_hash="b" * 64,
        meter_family=family,
        meter_id=f"meter-{index}",
        native_unit="seat" if family is ConsumptionFamily.SUBSCRIPTION else "native_unit",
        native_currency="native",
        quantity=2.0,
        evidence_status=MeterEvidenceStatus.MEASURED,
        entitlement_disposition="applicable",
        purchase_source="seat" if family is ConsumptionFamily.SUBSCRIPTION else "payg",
        evidence_source="authority",
        evidence_content_hash=f"{index + 1:x}" * 64,
        pricing_revision="pricing.v1" if coverage is CostCoverage.PRICED else None,
        rate_card_revision=None,
        billing_period="2026-09",
        calculation_method="source_reported",
        allocation_method="approved_allocation",
        cost_coverage=coverage,
        allocated_cost_usd=cost,
        recorded_at="2026-09-02T12:00:00+00:00",
    )


def _build(route: str, decision: AcceptanceDecision = AcceptanceDecision.ACCEPTED):
    profile = next(item for item in ROUTE_CAPABILITY_PROFILES if item.route_id == route)
    expected_legs = {
        "copilot_studio_byom": ("commercial", "foundry"),
        "foundry_work_iq": ("foundry", "work_iq"),
    }.get(route, (profile.controls[0].target_leg,))
    bindings = []
    for index, leg in enumerate(expected_legs):
        entry = _entry(
            route,
            index,
            family=(
                ConsumptionFamily.DIRECT_TOKEN
                if leg == "foundry"
                else ConsumptionFamily.NATIVE_CREDIT
            ),
            cost=4.0 + index,
        )
        authority = next(
            (
                control.authority
                for control in profile.controls
                if control.target_leg == leg
            ),
            "product_meter_authority",
        )
        bindings.append(
            LedgerBinding(
                entry=entry,
                entry_content_hash=_digest(entry.to_dict()),
                authority=authority,
                target_leg=leg,
                economic_view=(
                    EconomicView.FIXED if index == 0 else EconomicView.INCREMENTAL
                ),
                capacity_quantity=4.0 if index == 0 else 1.0,
                capacity_kind=(
                    CapacityKind.FIXED_SEAT
                    if index == 0
                    else CapacityKind.COMMITMENT
                ),
                allocation_revision="allocation.v1",
            )
        )
    return build_route_task_economics(
        route_id=route,
        completed_tasks=[
            {
                "task_id": f"task-{route}",
                "trajectory_id": f"trajectory-{route}",
                "segment_id": "hard",
                "segment_version": "v1",
                "completion_evidence_hash": "d" * 64,
            }
        ],
        acceptance_outcomes=[_outcome(route, decision)],
        ledger_bindings=bindings,
        allocation_period="2026-09",
        allocation_basis="monthly_task_allocation",
        allocation_basis_revision="allocation.v1",
        allocation_basis_content_hash="e" * 64,
        evidence_classification="measured",
    )


def test_all_nine_routes_preserve_filterable_native_economics() -> None:
    results = [_build(route) for route in ROUTES]

    assert len(results) == 9
    assert {item["route_id"] for item in results} == set(ROUTES)
    assert all(item["filters"]["route_ids"] == [item["route_id"]] for item in results)
    assert all(item["filters"]["meter_stacks"] for item in results)
    assert all(item["filters"]["policy_candidates"] for item in results)
    assert all(item["filters"]["segments"] for item in results)
    assert all(item["filters"]["authorities"] for item in results)
    assert all(item["overall"]["native_ledgers"] for item in results)


def test_hybrid_routes_keep_dual_ledgers_and_fixed_incremental_views() -> None:
    studio = _build("copilot_studio_byom")
    work_iq = _build("foundry_work_iq")

    assert set(studio["filters"]["target_legs"]) == {"commercial", "foundry"}
    assert set(work_iq["filters"]["target_legs"]) == {"foundry", "work_iq"}
    assert len(studio["overall"]["native_ledgers"]) == 2
    assert studio["overall"]["monetary_views"]["fixed_allocated"]["priced_total_usd"] == 4
    assert studio["overall"]["monetary_views"]["incremental"]["priced_total_usd"] == 5
    assert {item["capacity_kind"] for item in studio["overall"]["capacity_utilization"]} == {
        "fixed_seat",
        "commitment",
    }
    assert sorted(
        item["overage_quantity"]
        for item in studio["overall"]["capacity_utilization"]
    ) == [0.0, 1.0]


def test_unpriced_applicable_cost_is_visible_and_zero_acceptance_has_no_ratio() -> None:
    profile = next(item for item in ROUTE_CAPABILITY_PROFILES if item.route_id == "foundry")
    priced = _entry("foundry", 0)
    unpriced = _entry(
        "foundry",
        1,
        family=ConsumptionFamily.RESOURCE,
        coverage=CostCoverage.UNPRICED,
        cost=None,
    )
    result = build_route_task_economics(
        route_id="foundry",
        completed_tasks=[
            {
                "task_id": "task-foundry",
                "trajectory_id": "trajectory-foundry",
                "segment_id": "hard",
                "segment_version": "v1",
                "completion_evidence_hash": "d" * 64,
            }
        ],
        acceptance_outcomes=[_outcome("foundry", AcceptanceDecision.REJECTED)],
        ledger_bindings=[
            LedgerBinding(
                entry=entry,
                entry_content_hash=_digest(entry.to_dict()),
                authority="azure_tokengov",
                target_leg=profile.controls[0].target_leg,
                economic_view=view,
            )
            for entry, view in (
                (priced, EconomicView.FIXED),
                (unpriced, EconomicView.INCREMENTAL),
            )
        ],
        allocation_period="2026-09",
        allocation_basis="monthly",
        allocation_basis_revision="v1",
        allocation_basis_content_hash="e" * 64,
        evidence_classification="measured_partial",
    )

    monetary = result["overall"]["monetary_views"]
    assert monetary["priced_combined"]["priced_total_usd"] == 10
    assert monetary["priced_combined"]["cost_per_accepted_task_usd"] is None
    assert result["overall"]["coverage"]["complete"] is False
    assert result["overall"]["coverage"]["unpriced_applicable_entry_ids"] == [
        "entry-foundry-1"
    ]
    assert "partial_priced_cost_coverage" in result["warnings"]


def test_unavailable_or_excluded_evidence_cannot_be_priced() -> None:
    priced = _entry("foundry", 0)

    with pytest.raises(ValueError, match="cannot be represented as priced"):
        replace(
            priced,
            evidence_status=MeterEvidenceStatus.EXCLUDED,
            quantity=None,
            unavailable_reason="excluded from authoritative source",
        )


def test_route_economics_output_matches_published_schema() -> None:
    schema = json.loads(
        (ROOT / "data/contracts/route-task-economics.v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(_build("copilot_studio_byom"))
