from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from costgov.route_admission import (
    AdmissionEvidence,
    EvidenceState,
    assess_route_readiness,
)
from costgov.route_capabilities import EnforcementScope, route_capability_profile_for
from costgov.route_execution import (
    EXECUTION_AUTHORIZATION_SCHEMA_VERSION,
    AuthorityEvidence,
    AuthorityState,
    AuthorizationOutcome,
    BindingOutcome,
    CompositeExecutionAuthorization,
    EvidenceLabel,
    ExecutionAuthorizationStore,
    ExecutionMode,
    PolicyProvenance,
    authorize_route_execution,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 2)
NOW = "2026-09-02T16:00:00+00:00"
HASH = "a" * 64
ROUTES = (
    "included",
    "cowork",
    "agent_builder",
    "copilot_studio",
    "work_iq",
    "foundry",
    "github_copilot",
    "copilot_studio_byom",
    "foundry_work_iq",
)


def _readiness(route_id: str):
    profile = route_capability_profile_for(route_id, as_of=AS_OF)
    evidence = {
        requirement: AdmissionEvidence(
            requirement=requirement,
            state=EvidenceState.SATISFIED,
            authority="test-readiness-authority",
            evidence_revision="readiness.v1",
            content_hash="b" * 64,
            reason=None,
        )
        for requirement in profile["evidence_requirements"]
    }
    return assess_route_readiness(
        route_id=route_id,
        plan_receipt_hash=HASH,
        evidence=evidence,
        created_at=NOW,
        as_of=AS_OF,
    )


def _policy() -> PolicyProvenance:
    return PolicyProvenance(
        policy_id="tokengov",
        version="2026-09-02.1",
        content_hash="c" * 64,
        source="azure_app_configuration",
        label="production",
        etag="etag-1",
    )


def _authority_evidence(route_id: str) -> dict[str, AuthorityEvidence]:
    profile = route_capability_profile_for(route_id, as_of=AS_OF)
    result = {}
    for control in profile["controls"]:
        if control["enforcement_scope"] == EnforcementScope.ADVISORY.value:
            continue
        content_hash = (
            _policy().content_hash
            if control["authority"] == "azure_tokengov"
            else "d" * 64
        )
        result[control["control_id"]] = AuthorityEvidence(
            control_id=control["control_id"],
            authority=control["authority"],
            state=AuthorityState.SATISFIED,
            evidence_label=EvidenceLabel.MEASURED,
            evidence_id=f"evidence-{control['control_id']}",
            evidence_revision="authority.v1",
            content_hash=content_hash,
            observed_at="2026-09-02T15:00:00+00:00",
            valid_until="2026-09-03T15:00:00+00:00",
        )
    return result


def _authorization(route_id: str, **changes):
    profile = route_capability_profile_for(route_id, as_of=AS_OF)
    has_azure = any(
        control["authority"] == "azure_tokengov"
        for control in profile["controls"]
    )
    values = {
        "route_id": route_id,
        "readiness": _readiness(route_id),
        "govern_decision_id": "govern-selected-1",
        "govern_decision_hash": "e" * 64,
        "plan_receipt_id": "plan-1",
        "plan_receipt_hash": HASH,
        "candidate_id": "candidate-1",
        "candidate_version": "candidate.v1",
        "candidate_hash": "f" * 64,
        "task_id": "task-1",
        "trajectory_id": "trajectory-1",
        "segment_id": "hard",
        "segment_version": "segment.v1",
        "authority_evidence": _authority_evidence(route_id),
        "policy": _policy() if has_azure else None,
        "execution_mode": ExecutionMode.BILLABLE,
        "preflight_at": NOW,
        "as_of": AS_OF,
    }
    values.update(changes)
    return authorize_route_execution(**values)


@pytest.mark.parametrize("route_id", ROUTES)
def test_all_nine_routes_bind_only_their_declared_authorities(route_id: str) -> None:
    authorization = _authorization(route_id)
    profile = route_capability_profile_for(route_id, as_of=AS_OF)
    has_runtime = any(
        item["enforcement_scope"] == EnforcementScope.RUNTIME_ENFORCED.value
        for item in profile["controls"]
    )

    assert authorization.outcome is (
        AuthorizationOutcome.AUTHORIZED
        if has_runtime
        else AuthorizationOutcome.EXTERNALLY_AUTHORIZED
    )
    assert authorization.billable_execution_permitted is True
    assert {
        (item.control_id, item.authority)
        for item in authorization.bindings
        if not item.control_id.startswith("usage-source:")
    } == {
        (item["control_id"], item["authority"]) for item in profile["controls"]
    }
    assert any(
        item.outcome is BindingOutcome.CORRELATION_BOUNDARY
        for item in authorization.bindings
    )
    assert all(
        not item.tokengov_enforcement_event
        for item in authorization.bindings
        if item.authority != "azure_tokengov"
    )


def test_foundry_runtime_controls_bind_exact_azure_policy_revision() -> None:
    authorization = _authorization("foundry")
    runtime = [
        item
        for item in authorization.bindings
        if item.enforcement_scope is EnforcementScope.RUNTIME_ENFORCED
    ]

    assert runtime
    assert all(item.outcome is BindingOutcome.RUNTIME_BOUND for item in runtime)
    assert all(item.authority_evidence_hash == _policy().content_hash for item in runtime)
    assert all(item.tokengov_enforcement_event for item in runtime)
    assert authorization.policy == _policy()

    mismatched = _authority_evidence("foundry")
    mismatched["foundry-routing"] = replace(
        mismatched["foundry-routing"], content_hash="9" * 64
    )
    blocked = _authorization("foundry", authority_evidence=mismatched)
    assert blocked.outcome is AuthorizationOutcome.BLOCKED
    assert blocked.billable_execution_permitted is False
    assert "foundry-routing:runtime_policy_revision_mismatch" in blocked.blocked_reasons


def test_every_control_scope_has_an_explicit_non_fabricated_binding_outcome() -> None:
    expected = {
        EnforcementScope.RUNTIME_ENFORCED: BindingOutcome.RUNTIME_BOUND,
        EnforcementScope.CONTROL_PLANE_ENFORCED: BindingOutcome.CONTROL_PLANE_BOUND,
        EnforcementScope.PRODUCT_ADMIN_ENFORCED: BindingOutcome.PRODUCT_ADMIN_VERIFIED,
        EnforcementScope.DESIGN_TIME_ENFORCED: BindingOutcome.DESIGN_TIME_VERIFIED,
        EnforcementScope.EXTERNAL_REQUIREMENT: (
            BindingOutcome.EXTERNAL_REQUIREMENT_VERIFIED
        ),
        EnforcementScope.ADVISORY: BindingOutcome.ADVISORY_BOUNDARY,
        EnforcementScope.CORRELATION_ONLY: BindingOutcome.CORRELATION_BOUNDARY,
    }
    observed = {}
    for route_id in ROUTES:
        for binding in _authorization(route_id).bindings:
            observed.setdefault(binding.enforcement_scope, binding.outcome)
            assert binding.outcome is expected[binding.enforcement_scope]

    assert set(observed) == set(EnforcementScope)


@pytest.mark.parametrize(
    ("route_id", "expected_legs"),
    [
        ("copilot_studio_byom", {"commercial", "foundry"}),
        ("foundry_work_iq", {"foundry", "work_iq"}),
    ],
)
def test_hybrid_paths_keep_independent_leg_authorities(
    route_id: str, expected_legs: set[str]
) -> None:
    authorization = _authorization(route_id)
    leg_authorities = {
        item.target_leg: item.authority
        for item in authorization.bindings
        if item.outcome
        not in {
            BindingOutcome.ADVISORY_BOUNDARY,
            BindingOutcome.CORRELATION_BOUNDARY,
        }
    }

    assert expected_legs <= set(leg_authorities)
    assert leg_authorities["foundry"] == "azure_tokengov"
    assert any(authority != "azure_tokengov" for authority in leg_authorities.values())


def test_invalid_missing_stale_and_modeled_authority_fail_before_billable_execution():
    evidence = _authority_evidence("cowork")
    invalid = replace(evidence["product-access"], authority="azure_tokengov")
    stale = replace(
        evidence["spending-limit"],
        valid_until="2026-09-02T15:30:00+00:00",
    )
    modeled = replace(
        evidence["native-capacity"], evidence_label=EvidenceLabel.MODELED
    )
    evidence.update(
        {
            "product-access": invalid,
            "spending-limit": stale,
            "native-capacity": modeled,
        }
    )
    evidence.pop("overage")

    authorization = _authorization("cowork", authority_evidence=evidence)

    assert authorization.outcome is AuthorizationOutcome.BLOCKED
    assert authorization.evidence_label is EvidenceLabel.BLOCKED
    assert authorization.billable_execution_permitted is False
    assert authorization.authorized_legs == ()
    assert {
        item.reason
        for item in authorization.bindings
        if item.outcome is BindingOutcome.BLOCKED
    } == {
        "authority_mismatch",
        "authority_evidence_stale",
        "authority_cannot_be_modeled_or_blocked",
        "required_authority_evidence_missing",
    }


def test_simulated_authority_is_truthful_and_never_permits_billable_execution():
    evidence = {
        key: replace(item, evidence_label=EvidenceLabel.SIMULATED)
        for key, item in _authority_evidence("work_iq").items()
    }
    simulation = _authorization(
        "work_iq",
        authority_evidence=evidence,
        execution_mode=ExecutionMode.SIMULATION,
    )
    blocked_billable = _authorization("work_iq", authority_evidence=evidence)

    assert simulation.outcome is AuthorizationOutcome.SIMULATION_AUTHORIZED
    assert simulation.evidence_label is EvidenceLabel.SIMULATED
    assert simulation.billable_execution_permitted is False
    assert blocked_billable.outcome is AuthorizationOutcome.BLOCKED


def test_external_route_rejects_fabricated_azure_policy() -> None:
    with pytest.raises(ValueError, match="fabricated Azure policy"):
        _authorization("included", policy=_policy())


def test_authorization_hash_round_trip_and_store_replay_are_idempotent(tmp_path):
    authorization = _authorization("foundry_work_iq")
    store = ExecutionAuthorizationStore(tmp_path)

    first = store.record(authorization)
    replay = store.record(authorization)
    reopened = store.get(authorization.authorization_id)

    assert replay == first == reopened
    assert reopened.authorization.content_hash == authorization.content_hash
    assert reopened.authorization.to_dict() == authorization.to_dict()
    conflicting = replace(
        authorization, preflight_at="2026-09-02T16:00:01+00:00"
    )
    with pytest.raises(ValueError, match="replay conflicts"):
        store.record(conflicting)


def test_fresh_preflight_for_same_task_gets_new_immutable_authorization_id(tmp_path):
    first = _authorization("foundry_work_iq")
    second = _authorization(
        "foundry_work_iq", preflight_at="2026-09-02T16:00:01+00:00"
    )
    store = ExecutionAuthorizationStore(tmp_path)

    assert first.authorization_id != second.authorization_id
    assert store.record(first).authorization.authorization_id == first.authorization_id
    assert store.record(second).authorization.authorization_id == second.authorization_id


def test_rehydrated_blocked_authorization_cannot_claim_authorized_legs() -> None:
    blocked = _authorization(
        "work_iq",
        authority_evidence={
            key: replace(item, evidence_label=EvidenceLabel.MODELED)
            for key, item in _authority_evidence("work_iq").items()
        },
    )
    payload = blocked.to_dict()
    payload["authorized_legs"] = ["commercial"]

    with pytest.raises(ValueError, match="cannot carry authorized legs"):
        CompositeExecutionAuthorization.from_dict(payload)


def test_execution_schema_validates_runtime_serialization() -> None:
    schema = json.loads(
        (
            ROOT
            / "data"
            / "contracts"
            / "composite-execution-authorization.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    authorization = _authorization("copilot_studio_byom")
    Draft202012Validator(schema).validate(authorization.to_dict())

    assert schema["properties"]["schema_version"]["const"] == (
        EXECUTION_AUTHORIZATION_SCHEMA_VERSION
    )
    assert set(schema["$defs"]["binding"]["properties"]["outcome"]["enum"]) == {
        item.value for item in BindingOutcome
    }
