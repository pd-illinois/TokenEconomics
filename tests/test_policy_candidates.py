from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from costgov.experiment_contracts import ExperimentManifest
from costgov.policy_candidates import (
    POLICY_CANDIDATE_SCHEMA_VERSION,
    CandidateStatus,
    PolicyCandidate,
    PolicyCandidateStore,
    PolicyControl,
    validate_candidate_application,
    validate_candidate_binding,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIR = ROOT / "data" / "policy_candidates"
MANIFEST_DIR = ROOT / "data" / "experiments"


def _control() -> PolicyControl:
    return PolicyControl.from_value(
        "routing-mode",
        "routing",
        "execution.routing_mode",
        "balanced",
        authority="azure_tokengov",
        capability="model_routing",
        enforcement_scope="runtime_enforced",
    )


def _candidate() -> PolicyCandidate:
    return PolicyCandidate(
        schema_version=POLICY_CANDIDATE_SCHEMA_VERSION,
        candidate_id="candidate-1",
        version="v1",
        status=CandidateStatus.PROPOSED,
        created_at="2026-08-31T18:00:00+00:00",
        experiment_id="experiment-1",
        experiment_revision="v1",
        meter_stack_id="foundry-meter-stack",
        meter_stack_version="consumption-models.v1",
        meter_stack_content_hash="a" * 64,
        controls=(_control(),),
    )


def test_candidate_rejects_unsupported_controls_and_false_authority_claims():
    with pytest.raises(ValueError, match="unsupported"):
        PolicyControl.from_value(
            "tenant-license",
            "license_assignment",
            "tenant.license",
            "assigned",
            authority="azure_tokengov",
            capability="license_management",
            enforcement_scope="runtime_enforced",
        )
    with pytest.raises(ValueError, match="declared authority"):
        replace(_control(), authority="microsoft_365_admin")
    with pytest.raises(ValueError, match="not applied"):
        validate_candidate_application(
            _candidate(),
            {"execution.routing_mode": "quality"},
        )
    with pytest.raises(ValueError, match="unsupported by this runtime"):
        validate_candidate_application(_candidate(), {})


def test_external_governance_posture_requires_explicit_evidence_state():
    with pytest.raises(ValueError, match="external governance posture"):
        PolicyControl.from_value(
            "agent-posture",
            "external_governance_posture",
            "requirements.agent365",
            {"status": "satisfied"},
            authority="external_governance_authority",
            capability="posture_evidence",
            enforcement_scope="external_requirement",
        )


def test_candidate_store_is_append_only_and_detects_tampering(tmp_path):
    candidate = _candidate()
    store = PolicyCandidateStore(tmp_path)
    created = store.append(candidate)

    assert store.get(candidate.candidate_id, candidate.version) == created
    with pytest.raises(FileExistsError):
        store.append(candidate)

    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidate"]["controls"][0]["value"] = "quality"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get(candidate.candidate_id, candidate.version)


def test_reference_candidates_are_hash_bound_to_experiment_arms():
    manifests = {}
    for path in MANIFEST_DIR.glob("*policy-comparison*.json"):
        manifest = ExperimentManifest.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
        manifests[(manifest.experiment_id, manifest.revision)] = manifest

    for path in sorted(CANDIDATE_DIR.glob("*.json")):
        candidate = PolicyCandidate.from_dict(json.loads(path.read_text(encoding="utf-8")))
        manifest = manifests[(candidate.experiment_id, candidate.experiment_revision)]
        arm = next(
            arm
            for arm in manifest.arms
            if arm.policy_candidate.evidence_id == candidate.candidate_id
        )
        validate_candidate_binding(candidate, manifest, arm.arm_id)
        factors = {factor.path: factor.value_json for factor in arm.factors}
        controls = {control.path: control.value_json for control in candidate.controls}
        assert controls == factors


def test_policy_candidate_schema_matches_runtime_contract():
    schema = json.loads(
        (ROOT / "data/contracts/policy-candidate.v1.schema.json").read_text()
    )

    assert schema["properties"]["schema_version"]["const"] == (
        POLICY_CANDIDATE_SCHEMA_VERSION
    )
    assert "license_assignment" not in schema["$defs"]["control"]["properties"][
        "kind"
    ]["enum"]
