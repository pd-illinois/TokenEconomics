from __future__ import annotations

import json

import pytest

from costgov.policy_changes import PolicyChangeStore
from costgov.policy_store import (
    LoadedPolicy,
    PolicyLoadError,
    admit_receipt,
    load_policy_from_environment,
    validate_policy,
)


def _policy() -> dict:
    return {
        "schema_version": "1.0",
        "policy_id": "tokengov-production",
        "version": "2026-07-20.1",
        "status": "active",
        "effective_from": "2026-01-01T00:00:00Z",
        "admission": {
            "allowed_providers": ["azure_openai"],
            "allowed_models": ["gpt-4.1"],
            "require_pricing_verified": True,
            "max_model_cost_per_call_usd": 0.02,
        },
        "execution": {
            "routing_mode": "balanced",
            "semantic_cache": {"enabled": True, "score_threshold": 0.83},
            "budget": {"per_tenant_usd_per_run": 5.0, "hard_cap_action": "degrade"},
            "evaluation": {"min_quality": 0.8, "min_segment_samples": 2},
        },
        "mutation": {
            "mode": "evaluation_bound",
            "allowed_knobs": ["routing.mode", "semantic_cache.score_threshold"],
        },
    }


def test_policy_validation_rejects_inactive_or_incomplete_documents():
    policy = _policy()
    policy["status"] = "draft"
    with pytest.raises(PolicyLoadError, match="not active"):
        validate_policy(policy)


def test_policy_validation_rejects_invalid_evaluation_control():
    policy = _policy()
    policy["execution"]["evaluation"]["min_quality"] = "not-a-number"

    with pytest.raises(PolicyLoadError, match="min_quality"):
        validate_policy(policy)


def test_file_policy_requires_explicit_development_mode(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")
    monkeypatch.setenv("TOKENGOV_POLICY_SOURCE", "file")
    monkeypatch.setenv("TOKENGOV_POLICY_FILE", str(path))

    loaded = load_policy_from_environment()

    assert loaded.document["policy_id"] == "tokengov-production"
    assert loaded.provenance["source"] == "local_file"
    assert loaded.provenance["development_only"] is True


def test_azure_is_default_and_fails_closed_without_endpoint(monkeypatch):
    monkeypatch.delenv("TOKENGOV_POLICY_SOURCE", raising=False)
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    with pytest.raises(PolicyLoadError, match="AZURE_APPCONFIG_ENDPOINT"):
        load_policy_from_environment()


def test_admission_uses_receipt_evidence_and_policy_provenance():
    from hashlib import sha256

    snapshot = {
        "report_id": "RPT-1",
        "plan_id": "plan-1",
        "schema_version": "1.0",
        "created_at": "2026-07-20T00:00:00+00:00",
        "description": "RAG workload",
        "intake": {"model": "gpt-4.1"},
        "prediction": {
            "provider": "azure_openai",
            "model": "gpt-4.1",
            "pricing_verified": True,
            "cost_per_call": {"mean": 0.013},
        },
        "infrastructure": {"status": "not_estimated"},
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    receipt = {**snapshot, "receipt_id": "receipt-1", "content_hash": sha256(canonical.encode()).hexdigest()}
    loaded = LoadedPolicy(_policy(), {"source": "azure_app_configuration", "etag": "etag-1"})

    decision = admit_receipt(receipt, loaded)

    assert decision["status"] == "admitted"
    assert decision["policy"]["version"] == "2026-07-20.1"
    assert decision["policy"]["provenance"]["etag"] == "etag-1"
    assert all(check["passed"] for check in decision["checks"])


def test_admission_rejects_unverified_or_over_ceiling_prediction():
    receipt = {
        "report_id": "RPT-1",
        "plan_id": "plan-1",
        "schema_version": "1.0",
        "created_at": "2026-07-20T00:00:00+00:00",
        "description": "RAG workload",
        "intake": {},
        "prediction": {
            "provider": "azure_openai",
            "model": "gpt-4.1",
            "pricing_verified": False,
            "cost_per_call": {"mean": 0.05},
        },
        "infrastructure": {"status": "not_estimated"},
        "receipt_id": "receipt-1",
        "content_hash": "invalid",
    }
    decision = admit_receipt(
        receipt,
        LoadedPolicy(_policy(), {"source": "azure_app_configuration"}),
    )

    assert decision["status"] == "rejected"
    assert {check["name"] for check in decision["checks"] if not check["passed"]} == {
        "receipt_integrity",
        "pricing_verified",
        "model_cost_per_call",
    }


def test_admission_rejects_unknown_receipt_schema_before_hash_projection():
    with pytest.raises(PolicyLoadError, match="unsupported receipt schema"):
        admit_receipt(
            {"schema_version": "5.0"},
            LoadedPolicy(_policy(), {"source": "azure_app_configuration"}),
        )


def test_policy_change_is_a_validated_draft_without_mutating_active_policy(tmp_path):
    active = _policy()
    loaded = LoadedPolicy(active, {"source": "azure_app_configuration", "etag": "etag-1"})

    proposal = PolicyChangeStore(tmp_path).create(
        {
            "reason": "Reduce exposure while measured quality remains above target.",
            "proposed_version": "2026-07-20.2",
            "changes": {
                "admission.max_model_cost_per_call_usd": 0.015,
                "execution.routing_mode": "cost",
            },
        },
        loaded,
    )

    assert proposal["status"] == "draft"
    assert proposal["publication"]["azure_write_permitted"] is False
    assert proposal["base_policy"]["etag"] == "etag-1"
    assert proposal["proposed_policy"]["admission"]["max_model_cost_per_call_usd"] == 0.015
    assert active["admission"]["max_model_cost_per_call_usd"] == 0.02
    assert PolicyChangeStore(tmp_path).list()[0]["change_id"] == proposal["change_id"]