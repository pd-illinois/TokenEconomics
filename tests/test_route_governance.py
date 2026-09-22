from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pathlib import Path

from costgov.consumption_models import meter_stack_for
from costgov.planning import PlanStore
from costgov.route_capabilities import ROUTE_CAPABILITY_PROFILES
from costgov.route_governance import (
    ROUTE_CANDIDATE_SCHEMA_VERSION,
    ROUTE_DECISION_SCHEMA_VERSION,
    build_route_candidate,
    compare_route_candidates,
    evaluate_route_candidate,
)

ROOT = Path(__file__).resolve().parents[1]


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence(route_id: str) -> dict:
    profile = next(
        item for item in ROUTE_CAPABILITY_PROFILES if item.route_id == route_id
    )
    return {
        requirement: {
            "state": "satisfied",
            "authority": "test_evidence_authority",
            "evidence_revision": "test-evidence.v1",
            "content_hash": _hash({"route": route_id, "requirement": requirement}),
            "reason": None,
        }
        for requirement in profile.evidence_requirements
    }


def _constraints(cost: float = 1.0) -> dict:
    return {
        "task_contract": "task.v1",
        "segment_contract": "segment.v1",
        "acceptance_contract": "acceptance-rule.v1",
        "period": "monthly",
        "expected_complete_task_cost": cost,
        "acceptance": {
            "segments": [
                {
                    "segment_id": "default",
                    "outcome": "accepted",
                    "sample_count": 30,
                    "minimum_samples": 30,
                }
            ]
        },
        "tail_risk": {
            "budget": 10,
            "epsilon": 0.05,
            "breach_probability": 0.02,
            "evidence_classification": "calibrated",
        },
        "cost_coverage": {
            "applicable_cost": cost,
            "priced_cost": cost,
            "unpriced_cost_decision_bounded": False,
        },
    }


def _receipt(route_id: str, *, ready: bool = True, cost: float = 1.0) -> dict:
    intake = {"route": route_id, "govern_constraints": _constraints(cost)}
    if ready:
        intake["govern_evidence"] = _evidence(route_id)
    snapshot = {
        "report_id": "report-1",
        "plan_id": f"plan-{route_id}",
        "schema_version": "5.0",
        "created_at": "2026-09-02T16:00:00+00:00",
        "description": f"{route_id} workload",
        "intake": intake,
        "analysis": {},
        "confirmed_profile": {},
        "assumptions": [],
        "clarifications": [],
        "exclusions": [],
        "route": {"route_id": route_id},
        "commercial": {"status": "complete"},
        "purchase": None,
        "token_subforecast": None,
        "hybrid": None,
        "acceptance_assumption": None,
        "meter_stack": meter_stack_for(route_id),
        "trajectory_contract": {
            "schema_version": "trajectory-envelope.v1",
            "workload": {"workload_id": f"workload-{route_id}", "version": "1"},
            "segment_schema_version": "segment.v1",
            "prediction_binding": None,
            "policy_binding": None,
        },
        "prediction": (
            {
                "provider": "azure_openai",
                "model": "gpt-test",
                "pricing_verified": True,
                "pricing_version": "test-pricing.v1",
            }
            if route_id in {"foundry", "copilot_studio_byom", "foundry_work_iq"}
            else {}
        ),
        "infrastructure": (
            {"status": "estimated"}
            if route_id in {"foundry", "copilot_studio_byom", "foundry_work_iq"}
            else {"status": "not_estimated"}
        ),
    }
    content_hash = _hash(snapshot)
    return {
        "receipt_id": f"plan_{content_hash[:20]}",
        **snapshot,
        "content_hash": content_hash,
    }


@pytest.mark.parametrize(
    "route_id", [item.route_id for item in ROUTE_CAPABILITY_PROFILES]
)
def test_all_routes_produce_hash_bound_eligible_candidates(route_id):
    candidate = build_route_candidate(_receipt(route_id))
    decision = evaluate_route_candidate(candidate)

    assert candidate["route_id"] == route_id
    assert candidate["readiness"]["outcome"] == "ready_for_admission"
    assert candidate["readiness"]["plan_receipt_hash"] == candidate["receipt_hash"]
    assert candidate["capability_profile"]["content_hash"] == (
        candidate["readiness"]["capability_profile_hash"]
    )
    assert decision["status"] == "eligible"
    assert all(check["passed"] for check in decision["checks"])


def test_missing_acceptance_evidence_is_route_readiness_block_not_model_error():
    candidate = build_route_candidate(_receipt("copilot_studio", ready=False))
    decision = evaluate_route_candidate(candidate)

    assert candidate["readiness"]["outcome"] == "blocked"
    assert "acceptance_rule" in candidate["readiness"]["missing_requirements"]
    assert decision["status"] == "blocked"
    assert not any("provider" in reason for reason in candidate["readiness"]["reason_codes"])


def test_modeled_percentile_does_not_pass_tail_probability_constraint():
    receipt = _receipt("foundry")
    receipt["intake"]["govern_constraints"]["tail_risk"][
        "evidence_classification"
    ] = "modeled"
    candidate = build_route_candidate(receipt)
    decision = evaluate_route_candidate(candidate)

    tail = next(
        check for check in decision["checks"] if check["name"] == "monetary_tail_risk"
    )
    assert tail["outcome"] == "failed"
    assert decision["status"] == "blocked"


def test_candidate_hash_is_deterministic_and_sensitive_to_controls():
    receipt = _receipt("work_iq")
    first = build_route_candidate(receipt)
    second = build_route_candidate(copy.deepcopy(receipt))
    changed_receipt = copy.deepcopy(receipt)
    changed_receipt["intake"]["candidate_controls"] = {"api_call_limit": 5}
    changed = build_route_candidate(changed_receipt)

    assert first == second
    assert first["content_hash"] != changed["content_hash"]


def test_comparison_rejects_incompatible_contracts_and_selects_least_cost():
    expensive = build_route_candidate(_receipt("cowork", cost=2.0))
    cheap = build_route_candidate(_receipt("work_iq", cost=1.0))

    selected = compare_route_candidates([expensive, cheap])
    assert selected["status"] == "selected"
    assert selected["selected_candidate_id"] == cheap["candidate_id"]

    incompatible = copy.deepcopy(cheap)
    incompatible["constraint_inputs"]["period"] = "annual"
    with pytest.raises(ValueError, match="identical task"):
        compare_route_candidates([expensive, incompatible])


def test_comparison_requires_declared_contracts() -> None:
    first = build_route_candidate(_receipt("cowork"))
    second = build_route_candidate(_receipt("work_iq"))
    for candidate in (first, second):
        candidate["constraint_inputs"].pop("task_contract")

    with pytest.raises(ValueError, match="requires declared"):
        compare_route_candidates([first, second])


def test_explicit_evidence_cannot_upgrade_derived_pricing_failure() -> None:
    receipt = _receipt("foundry")
    receipt["prediction"] = {
        "provider": "azure_openai",
        "model": "gpt-test",
        "pricing_verified": False,
    }
    candidate = build_route_candidate(receipt)
    verified_pricing = next(
        item
        for item in candidate["readiness"]["evidence"]
        if item["requirement"] == "verified_pricing"
    )

    assert verified_pricing["state"] == "failed"
    assert verified_pricing["reason"] == "model_pricing_not_verified"


def test_plan_store_persists_candidate_without_rewriting_receipt(tmp_path):
    store = PlanStore(tmp_path)
    receipt = _receipt("github_copilot")
    plan_id = receipt["plan_id"]
    plan_root = tmp_path / plan_id
    receipt_path = plan_root / "receipts" / f"{receipt['receipt_id']}.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    session = {
        "plan_id": plan_id,
        "report_id": receipt["report_id"],
        "status": "complete",
        "receipt_id": receipt["receipt_id"],
        "receipt_hash": receipt["content_hash"],
        "govern_candidate": None,
        "govern_handoff": None,
        "created_at": receipt["created_at"],
        "updated_at": receipt["created_at"],
    }
    (plan_root / "session.json").write_text(json.dumps(session), encoding="utf-8")
    before = receipt_path.read_bytes()

    first = store.create_govern_candidate(plan_id)
    second = store.create_govern_candidate(plan_id)

    assert first == second
    assert receipt_path.read_bytes() == before
    assert store.get(plan_id)["govern_candidate"]["content_hash"] == first["content_hash"]

    interrupted = store.get(plan_id)
    interrupted["govern_candidate"] = None
    (plan_root / "session.json").write_text(
        json.dumps(interrupted), encoding="utf-8"
    )
    replayed = store.create_govern_candidate(plan_id)
    handoff = store.create_route_govern_handoff(plan_id)

    assert replayed == first
    assert handoff["candidate_hash"] == first["content_hash"]
    assert store.get(plan_id)["govern_candidate"]["content_hash"] == first["content_hash"]
    assert len(list((plan_root / "govern_candidates").glob("*.json"))) == 1


def test_route_govern_schemas_match_runtime_contracts():
    candidate_schema = json.loads(
        (
            ROOT / "data" / "contracts" / "route-govern-candidate.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    decision_schema = json.loads(
        (
            ROOT / "data" / "contracts" / "route-govern-decision.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert candidate_schema["properties"]["schema_version"]["const"] == (
        ROUTE_CANDIDATE_SCHEMA_VERSION
    )
    assert decision_schema["properties"]["schema_version"]["const"] == (
        ROUTE_DECISION_SCHEMA_VERSION
    )
