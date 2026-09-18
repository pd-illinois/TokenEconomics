"""Immutable route-aware Govern candidates and constraint decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping

from .route_admission import (
    AdmissionEvidence,
    EvidenceState,
    ReadinessOutcome,
    assess_route_readiness,
)
from .route_capabilities import route_capability_profile_for

ROUTE_CANDIDATE_SCHEMA_VERSION = "route-govern-candidate.v1"
ROUTE_DECISION_SCHEMA_VERSION = "route-govern-decision.v1"


class ConstraintOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _route_id(receipt: Mapping[str, Any]) -> str:
    route = receipt.get("route")
    if not isinstance(route, Mapping):
        raise ValueError("route-aware Govern requires a route-bound Plan receipt")
    return _required(route.get("route_id"), "route.route_id")


def _satisfied(requirement: str, authority: str, revision: str, value: object) -> AdmissionEvidence:
    return AdmissionEvidence(
        requirement=requirement,
        state=EvidenceState.SATISFIED,
        authority=authority,
        evidence_revision=revision,
        content_hash=_hash(value),
        reason=None,
    )


def _failed(requirement: str, reason: str, value: object | None = None) -> AdmissionEvidence:
    return AdmissionEvidence(
        requirement=requirement,
        state=EvidenceState.FAILED,
        authority=None,
        evidence_revision=None,
        content_hash=_hash(value) if value is not None else None,
        reason=reason,
    )


def _auto_evidence(receipt: Mapping[str, Any]) -> dict[str, AdmissionEvidence]:
    route_id = _route_id(receipt)
    intake = receipt.get("intake") if isinstance(receipt.get("intake"), Mapping) else {}
    commercial_input = (
        intake.get("commercial") if isinstance(intake.get("commercial"), Mapping) else {}
    )
    commercial = (
        receipt.get("commercial")
        if isinstance(receipt.get("commercial"), Mapping)
        else {}
    )
    prediction = (
        receipt.get("prediction")
        if isinstance(receipt.get("prediction"), Mapping)
        else {}
    )
    infrastructure = (
        receipt.get("infrastructure")
        if isinstance(receipt.get("infrastructure"), Mapping)
        else {}
    )
    purchase = receipt.get("purchase")
    result: dict[str, AdmissionEvidence] = {}

    if purchase:
        for requirement in ("license_assignment", "seat_assignment"):
            result[requirement] = _satisfied(
                requirement, "plan_purchase_evidence", "purchase-portfolio.v1", purchase
            )
    if commercial.get("status") == "complete":
        for requirement in (
            "included_use_eligibility",
            "internal_product_boundary",
            "entitlement",
        ):
            result[requirement] = _satisfied(
                requirement,
                "plan_commercial_forecast",
                str(commercial.get("schema_version") or "commercial-forecast.v1"),
                commercial,
            )

    scenario = commercial_input.get("scenario_prior")
    if isinstance(scenario, Mapping):
        for requirement in (
            "approved_scenario_prior",
            "rate_or_scenario_prior",
            "work_iq_rate_or_prior",
        ):
            result[requirement] = _satisfied(
                requirement,
                "plan_scenario_prior",
                str(scenario.get("evidence_version") or "scenario-prior.v1"),
                scenario,
            )
        product = scenario.get("product")
        if product:
            result["api_family"] = _satisfied(
                "api_family", "plan_route_intake", "commercial-route.v2", product
            )

    rate_card = commercial.get("rate_card")
    if rate_card:
        for requirement in ("rate_card", "copilot_rate_card"):
            result[requirement] = _satisfied(
                requirement,
                "published_product_rate_card",
                str(rate_card.get("version") or "rate-card.v1"),
                rate_card,
            )

    if commercial_input.get("plan_id"):
        result["github_plan"] = _satisfied(
            "github_plan",
            "github_plan_intake",
            "github-plan-input.v1",
            commercial_input["plan_id"],
        )
    if commercial_input.get("seat_count"):
        result["seat_assignment"] = _satisfied(
            "seat_assignment",
            "github_seat_intake",
            "github-seat-input.v1",
            commercial_input["seat_count"],
        )
    if commercial.get("credit_definition") or commercial.get("rate_card"):
        result["model_pricing"] = _satisfied(
            "model_pricing",
            "github_pricing_evidence",
            str((commercial.get("rate_card") or {}).get("version") or "github-rate-card.v1"),
            commercial.get("credit_definition") or commercial.get("rate_card"),
        )
    if "additional_usage_enabled" in commercial_input:
        result["budget_hierarchy"] = _satisfied(
            "budget_hierarchy",
            "github_budget_intake",
            "github-budget-input.v1",
            {"additional_usage_enabled": commercial_input["additional_usage_enabled"]},
        )

    if prediction.get("provider") and prediction.get("model"):
        result["released_model"] = _satisfied(
            "released_model",
            "plan_model_release",
            str(prediction.get("pricing_version") or "model-release.v1"),
            {"provider": prediction["provider"], "model": prediction["model"]},
        )
    if prediction:
        pricing_evidence = prediction.get("pricing_verified") is True
        for requirement in ("verified_pricing", "foundry_model_pricing"):
            result[requirement] = (
                _satisfied(
                    requirement,
                    "plan_pricing_evidence",
                    str(prediction.get("pricing_version") or "model-pricing.v1"),
                    prediction,
                )
                if pricing_evidence
                else _failed(requirement, "model_pricing_not_verified", prediction)
            )

    if infrastructure:
        infrastructure_revision = str(
            infrastructure.get("schema_version") or "infrastructure-estimate.v1"
        )
        result["infrastructure_coverage"] = (
            _satisfied(
                "infrastructure_coverage",
                "plan_infrastructure_evidence",
                infrastructure_revision,
                infrastructure,
            )
            if infrastructure.get("status") == "estimated"
            and (
                receipt.get("schema_version") != "6.0"
                or infrastructure.get("confirmed") is True
            )
            else _failed(
                "infrastructure_coverage",
                "applicable_infrastructure_cost_not_estimated",
                infrastructure,
            )
        )

    if route_id in {"copilot_studio_byom", "foundry_work_iq"}:
        assumption = commercial_input.get("hybrid_dependence_assumption")
        if assumption:
            result["hybrid_dependence_assumption"] = _satisfied(
                "hybrid_dependence_assumption",
                "plan_route_intake",
                "hybrid-dependence.v1",
                assumption,
            )
    return result


def build_route_candidate(
    receipt: Mapping[str, Any], *,
    evidence: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    assessment_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic candidate linked to an immutable Plan receipt."""
    route_id = _route_id(receipt)
    created_at = _required(receipt.get("created_at"), "receipt.created_at")
    profile = route_capability_profile_for(
        route_id, as_of=date.fromisoformat(created_at[:10])
    )
    intake = receipt.get("intake") if isinstance(receipt.get("intake"), Mapping) else {}
    explicit = evidence if evidence is not None else intake.get("govern_evidence")
    if explicit is None:
        explicit = {}
    if not isinstance(explicit, Mapping):
        raise ValueError("govern_evidence must be an object")
    applicable = set(profile["evidence_requirements"])
    evidence: dict[str, AdmissionEvidence | Mapping[str, Any]] = {
        key: value
        for key, value in _auto_evidence(receipt).items()
        if key in applicable
    }
    for requirement, value in explicit.items():
        derived = evidence.get(requirement)
        supplied = (
            value
            if isinstance(value, AdmissionEvidence)
            else AdmissionEvidence.from_dict(requirement, value)
        )
        if (
            isinstance(derived, AdmissionEvidence)
            and derived.state is EvidenceState.FAILED
            and supplied.state is EvidenceState.SATISFIED
        ):
            continue
        evidence[requirement] = supplied
    readiness = assess_route_readiness(
        route_id=route_id,
        plan_receipt_hash=_required(receipt.get("content_hash"), "receipt.content_hash"),
        evidence=evidence,
        created_at=created_at,
        as_of=date.fromisoformat(created_at[:10]),
    )
    readiness_payload = {
        **readiness.to_dict(),
        "content_hash": readiness.content_hash,
    }
    candidate_snapshot = {
        "schema_version": ROUTE_CANDIDATE_SCHEMA_VERSION,
        "created_at": created_at,
        "report_id": receipt["report_id"],
        "plan_id": receipt["plan_id"],
        "receipt_id": receipt["receipt_id"],
        "receipt_schema_version": receipt["schema_version"],
        "receipt_hash": receipt["content_hash"],
        "route_id": route_id,
        "meter_stack": receipt["meter_stack"],
        "capability_profile": profile,
        "candidate_controls": intake.get("candidate_controls") or {},
        "route_assumptions": intake.get("route_assumptions") or {},
        "constraint_inputs": constraints if constraints is not None else intake.get("govern_constraints") or {},
        "readiness": readiness_payload,
        "hybrid": receipt.get("hybrid"),
        "token_subforecast": receipt.get("token_subforecast"),
    }
    if assessment_context is not None:
        candidate_snapshot["assessment_context"] = dict(assessment_context)
    content_hash = _hash(candidate_snapshot)
    return {
        "candidate_id": f"candidate_{content_hash[:20]}",
        **candidate_snapshot,
        "content_hash": content_hash,
    }


def _constraint(
    name: str,
    outcome: ConstraintOutcome,
    reason: str,
    actual: object = None,
    expected: object = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "outcome": outcome.value,
        "passed": outcome is ConstraintOutcome.PASSED,
        "reason": reason,
        "actual": actual,
        "expected": expected,
    }


def evaluate_route_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate shared route constraints without performing execution or mutation."""
    readiness = candidate["readiness"]
    checks: list[dict[str, Any]] = []
    if readiness["outcome"] == ReadinessOutcome.BLOCKED.value:
        checks.append(
            _constraint(
                "route_readiness",
                ConstraintOutcome.FAILED,
                "required_route_evidence_missing_or_failed",
                readiness["reason_codes"],
                "ready_for_admission",
            )
        )
    elif readiness["outcome"] == ReadinessOutcome.INCONCLUSIVE.value:
        checks.append(
            _constraint(
                "route_readiness",
                ConstraintOutcome.INCONCLUSIVE,
                "route_evidence_inconclusive",
                readiness["reason_codes"],
                "ready_for_admission",
            )
        )
    else:
        checks.append(
            _constraint(
                "route_readiness",
                ConstraintOutcome.PASSED,
                "all_applicable_route_evidence_satisfied",
                readiness["outcome"],
                "ready_for_admission",
            )
        )
    for item in readiness["evidence"]:
        state = item["state"]
        outcome = (
            ConstraintOutcome.PASSED
            if state == EvidenceState.SATISFIED.value
            else ConstraintOutcome.INCONCLUSIVE
            if state == EvidenceState.INCONCLUSIVE.value
            else ConstraintOutcome.FAILED
        )
        checks.append(
            _constraint(
                f"route_evidence:{item['requirement']}",
                outcome,
                item.get("reason") or "versioned_route_evidence_satisfied",
                {
                    "state": state,
                    "authority": item.get("authority"),
                    "evidence_revision": item.get("evidence_revision"),
                    "content_hash": item.get("content_hash"),
                },
                "satisfied",
            )
        )

    inputs = candidate.get("constraint_inputs") or {}
    if not isinstance(inputs, Mapping):
        raise ValueError("constraint_inputs must be an object")

    acceptance = inputs.get("acceptance")
    if not isinstance(acceptance, Mapping):
        checks.append(
            _constraint(
                "segment_acceptance",
                ConstraintOutcome.INCONCLUSIVE,
                "explicit_segment_acceptance_outcomes_not_supplied",
                None,
                "all_material_segments_accepted",
            )
        )
    else:
        segments = acceptance.get("segments")
        sufficient = (
            isinstance(segments, list)
            and bool(segments)
            and all(
                isinstance(item, Mapping)
                and item.get("outcome") == "accepted"
                and int(item.get("sample_count", 0)) >= int(item.get("minimum_samples", 1))
                for item in segments
            )
        )
        checks.append(
            _constraint(
                "segment_acceptance",
                ConstraintOutcome.PASSED if sufficient else ConstraintOutcome.FAILED,
                (
                    "all_material_segments_have_sufficient_accepted_outcomes"
                    if sufficient
                    else "segment_acceptance_floor_or_sample_requirement_failed"
                ),
                segments,
                "accepted_with_sufficient_samples",
            )
        )

    tail = inputs.get("tail_risk")
    if not isinstance(tail, Mapping):
        checks.append(
            _constraint(
                "monetary_tail_risk",
                ConstraintOutcome.INCONCLUSIVE,
                "explicit_budget_epsilon_and_breach_probability_not_supplied",
                None,
                "calibrated_or_labeled_probability_bound",
            )
        )
    else:
        required = ("budget", "epsilon", "breach_probability", "evidence_classification")
        complete = all(key in tail for key in required)
        classification = tail.get("evidence_classification")
        probability = tail.get("breach_probability")
        epsilon = tail.get("epsilon")
        passed = (
            complete
            and classification in {"measured", "calibrated"}
            and isinstance(probability, (int, float))
            and isinstance(epsilon, (int, float))
            and probability <= epsilon
        )
        outcome = (
            ConstraintOutcome.PASSED
            if passed
            else ConstraintOutcome.FAILED
            if complete
            else ConstraintOutcome.INCONCLUSIVE
        )
        checks.append(
            _constraint(
                "monetary_tail_risk",
                outcome,
                (
                    "breach_probability_within_explicit_tolerance"
                    if passed
                    else "modeled_percentile_is_not_a_calibrated_tail_bound"
                    if classification not in {"measured", "calibrated"}
                    else "tail_risk_requirement_not_met"
                ),
                tail,
                "breach_probability <= epsilon",
            )
        )

    coverage = inputs.get("cost_coverage")
    if not isinstance(coverage, Mapping):
        checks.append(
            _constraint(
                "complete_task_cost_coverage",
                ConstraintOutcome.INCONCLUSIVE,
                "applicable_cost_coverage_not_supplied",
                None,
                "all_material_costs_priced_or_decision_bounded",
            )
        )

    else:
        applicable = float(coverage.get("applicable_cost", 0))
        priced = float(coverage.get("priced_cost", 0))
        decision_bounded = bool(coverage.get("unpriced_cost_decision_bounded", False))
        passed = applicable >= 0 and priced <= applicable and (
            priced == applicable or decision_bounded
        )
        checks.append(
            _constraint(
                "complete_task_cost_coverage",
                ConstraintOutcome.PASSED if passed else ConstraintOutcome.FAILED,
                (
                    "applicable_cost_is_priced_or_decision_bounded"
                    if passed
                    else "material_unpriced_cost_is_not_decision_bounded"
                ),
                coverage,
                "priced_cost == applicable_cost or bounded exclusion",
            )
        )

    expected_cost = inputs.get("expected_complete_task_cost")
    cost_is_valid = (
        isinstance(expected_cost, (int, float))
        and not isinstance(expected_cost, bool)
        and expected_cost >= 0
    )
    checks.append(
        _constraint(
            "expected_complete_task_cost",
            ConstraintOutcome.PASSED
            if cost_is_valid
            else ConstraintOutcome.INCONCLUSIVE,
            (
                "expected_complete_task_cost_is_explicit"
                if cost_is_valid
                else "expected_complete_task_cost_not_supplied"
            ),
            expected_cost,
            "non_negative_sourced_cost",
        )
    )

    outcomes = {item["outcome"] for item in checks}
    if ConstraintOutcome.FAILED.value in outcomes:
        status = "blocked"
    elif ConstraintOutcome.INCONCLUSIVE.value in outcomes:
        status = "inconclusive"
    elif readiness["outcome"] == ReadinessOutcome.ADVISORY_ONLY.value:
        status = "advisory_only"
    else:
        status = "eligible"
    snapshot = {
        "schema_version": ROUTE_DECISION_SCHEMA_VERSION,
        "candidate_id": candidate["candidate_id"],
        "candidate_hash": candidate["content_hash"],
        "route_id": candidate["route_id"],
        "evaluated_at": candidate["created_at"],
        "status": status,
        "checks": checks,
        "native_meter": candidate["meter_stack"],
        "mutation": {"performed": False, "mode": "review_required"},
    }
    content_hash = _hash(snapshot)
    return {
        "decision_id": f"route_decision_{content_hash[:20]}",
        **snapshot,
        "content_hash": content_hash,
    }


def compare_route_candidates(candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Select least expected cost only among contract-compatible eligible candidates."""
    if not candidates:
        raise ValueError("at least one candidate is required")
    decisions = [evaluate_route_candidate(item) for item in candidates]
    contract_rows = [
        {
            "task_contract": (item.get("constraint_inputs") or {}).get("task_contract"),
            "segment_contract": (item.get("constraint_inputs") or {}).get("segment_contract"),
            "acceptance_contract": (item.get("constraint_inputs") or {}).get(
                "acceptance_contract"
            ),
            "period": (item.get("constraint_inputs") or {}).get("period"),
        }
        for item in candidates
    ]
    if any(value is None for row in contract_rows for value in row.values()):
        raise ValueError(
            "cross-route comparison requires declared task, segment, acceptance, and period contracts"
        )
    contracts = {_canonical(row) for row in contract_rows}
    if len(contracts) != 1:
        raise ValueError(
            "cross-route comparison requires identical task, segment, acceptance, and period contracts"
        )
    eligible = [
        (candidate, decision)
        for candidate, decision in zip(candidates, decisions)
        if decision["status"] == "eligible"
    ]
    if not eligible:
        statuses = {decision["status"] for decision in decisions}
        if "inconclusive" in statuses:
            status = "no_eligible_candidate_inconclusive"
        elif statuses == {"advisory_only"}:
            status = "no_eligible_candidate_advisory_only"
        else:
            status = "no_eligible_candidate_blocked"
        return {
            "status": status,
            "selected_candidate_id": None,
            "decisions": decisions,
        }
    selected, _ = min(
        eligible,
        key=lambda pair: float(
            (pair[0].get("constraint_inputs") or {}).get("expected_complete_task_cost")
        ),
    )
    return {
        "status": "selected",
        "selected_candidate_id": selected["candidate_id"],
        "decisions": decisions,
    }
