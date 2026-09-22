"""Deterministic nine-route software validation and external-proof gap register."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .route_capabilities import ROUTE_CAPABILITY_PROFILES

NINE_PATH_VALIDATION_SCHEMA_VERSION = "nine-path-validation.v1"
NINE_PATH_VALIDATION_REVISION = "2026-09-02.1"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


_MEASURED_PROOFS = {
    "foundry": {
        "status": "measured_prototype_execution",
        "evidence_reference": "docs/decision.md#D43",
        "evidence_id": "trajectory-605b9b868a5d45068d20c45e7973590b",
        "scope": "policy-bound Foundry agent trajectory",
        "limitations": [
            "pricing_not_decision_grade",
            "segment_acceptance_not_established",
            "production_validation_not_claimed",
        ],
    },
    "work_iq": {
        "status": "measured_single_task_portability",
        "evidence_reference": "docs/decision.md#D56",
        "evidence_id": "portability-1de8f8c274f44e947e95c01978df114b",
        "scope": "privacy-minimized Work IQ plus Graph count trajectory",
        "limitations": [
            "copilot_credit_actuals_unavailable",
            "single_task_not_decision_grade",
            "production_validation_not_claimed",
        ],
    },
}

_BLOCKED_GAPS = {
    "included": ["tenant_license_assignment_export", "representative_acceptance_set"],
    "cowork": ["tenant_copilot_credit_actuals", "public_deterministic_task_rate"],
    "agent_builder": ["tenant_license_assignment_export", "task_level_usage_meter"],
    "copilot_studio": ["power_platform_usage_export", "representative_acceptance_set"],
    "github_copilot": ["github_billing_export", "task_level_acceptance_join"],
    "copilot_studio_byom": [
        "commercial_and_azure_dual_ledger_actuals",
        "cross_leg_acceptance_evidence",
    ],
    "foundry_work_iq": [
        "work_iq_copilot_credit_actuals",
        "cross_leg_task_cost_join",
    ],
}


def nine_path_validation_matrix() -> dict[str, Any]:
    rows = []
    for profile in ROUTE_CAPABILITY_PROFILES:
        proof = _MEASURED_PROOFS.get(profile.route_id)
        gaps = _BLOCKED_GAPS.get(profile.route_id, [])
        if proof is None:
            proof = {
                "status": "blocked_external_validation",
                "evidence_reference": None,
                "evidence_id": None,
                "scope": "no source-verifiable tenant execution supplied",
                "limitations": gaps,
            }
        rows.append(
            {
                "route_id": profile.route_id,
                "capability_profile_version": profile.version,
                "capability_profile_hash": profile.content_hash,
                "software_contract_validation": "passed",
                "deterministic_fixture": (
                    "tests/test_route_execution_contracts.py::"
                    "test_all_nine_routes_bind_only_their_declared_authorities"
                    f"[{profile.route_id}]"
                ),
                "hybrid_dual_ledger_required": profile.route_id
                in {"copilot_studio_byom", "foundry_work_iq"},
                "source_verifiable_execution": proof,
                "production_validated": False,
                "remaining_gaps": proof["limitations"],
            }
        )
    payload = {
        "schema_version": NINE_PATH_VALIDATION_SCHEMA_VERSION,
        "revision": NINE_PATH_VALIDATION_REVISION,
        "evidence_classification": "mixed_measured_and_blocked",
        "program_status": "software_validated_external_proof_partial",
        "lifecycle": [
            "predict",
            "compare_policy",
            "admit",
            "execute",
            "evaluate",
            "respond",
            "reconcile",
            "learn",
        ],
        "routes": rows,
        "software_contract_families": [
            "route-capability-profile.v1",
            "route-admission-readiness.v1",
            "route-govern-candidate.v1",
            "route-govern-decision.v1",
            "composite-execution-authorization.v1",
            "route-usage-observation.v1",
            "workload-acceptance-pack.v1",
            "route-acceptance-outcome.v1",
            "route-task-economics.v1",
            "route-response-transition.v1",
            "source-actual-import.v1",
            "composite-route-reconciliation.v1",
            "route-learning-receipt.v1",
            "forecast-learning-link.v1",
        ],
        "claims": {
            "guaranteed_savings": False,
            "guaranteed_quality": False,
            "nine_path_production_validation": False,
            "modeled_percentile_is_calibrated_tail_bound": False,
        },
    }
    return {**payload, "content_hash": _digest(payload)}
