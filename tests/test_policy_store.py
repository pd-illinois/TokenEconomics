from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from costgov.policy_changes import PolicyChangeStore
from costgov.policy_store import (
    LoadedPolicy,
    PolicyLoadError,
    admit_receipt,
    load_policy_from_environment,
    validate_measurement_policy,
    validate_policy,
)

ROOT = Path(__file__).resolve().parents[1]


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


def test_measurement_policy_v1_remains_compatible():
    policy = json.loads(
        (
            ROOT
            / "data"
            / "policies"
            / "tokengov-te003-live-proof.2026-09-17.measurement.1.json"
        ).read_text(encoding="utf-8")
    )

    assert validate_policy(policy)["measurement"]["schema_version"] == (
        "workload-measurement-policy.v1"
    )


def test_exact_merged_campaign_policy_is_valid_v2_fixture():
    policy = json.loads(
        (
            ROOT
            / "data"
            / "policies"
            / "tokengov-te003-live-proof.2026-09-18.campaign.1.json"
        ).read_text(encoding="utf-8")
    )

    assert validate_policy(policy)["measurement"]["schema_version"] == (
        "workload-measurement-policy.v2"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_questions", 26),
        ("max_output_tokens", 4097),
        ("max_elapsed_seconds", 601),
        ("max_campaign_repetitions", 101),
        ("max_campaign_questions", 2501),
        ("max_evaluation_runs", 101),
        ("max_evaluation_rows_per_run", 26),
        ("campaign_observed_model_cost_stop_usd", 25.01),
        ("require_explicit_campaign_id", False),
    ],
)
def test_measurement_policy_v2_fails_closed_on_bounds(field, value):
    measurement = _campaign_measurement()
    measurement[field] = value

    with pytest.raises(PolicyLoadError, match=field):
        validate_measurement_policy(measurement)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"max_campaign_repetitions": 2, "max_campaign_questions": 51},
            "repetitions",
        ),
        (
            {
                "max_questions": 10,
                "max_campaign_questions": 1000,
                "max_evaluation_rows_per_run": 11,
            },
            "max_evaluation_rows_per_run",
        ),
        (
            {
                "observed_model_cost_stop_usd": 1.0,
                "campaign_observed_model_cost_stop_usd": 0.5,
            },
            "per-execution",
        ),
    ],
)
def test_measurement_policy_v2_fails_closed_on_relations(changes, message):
    measurement = _campaign_measurement()
    measurement.update(changes)

    with pytest.raises(PolicyLoadError, match=message):
        validate_measurement_policy(measurement)


def _campaign_measurement() -> dict:
    policy = json.loads(
        (
            ROOT
            / "data"
            / "policies"
            / "tokengov-te003-live-proof.2026-09-18.campaign.1.json"
        ).read_text(encoding="utf-8")
    )
    return deepcopy(policy["measurement"])


def test_file_policy_requires_explicit_development_mode(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")
    monkeypatch.setenv("TOKENGOV_POLICY_SOURCE", "file")
    monkeypatch.setenv("TOKENGOV_POLICY_FILE", str(path))

    loaded = load_policy_from_environment()

    assert loaded.document["policy_id"] == "tokengov-production"
    assert loaded.provenance["source"] == "local_file"
    assert loaded.provenance["label"] == "development:2026-07-20.1"
    assert len(loaded.provenance["etag"]) == 64
    assert loaded.provenance["development_only"] is True


def test_azure_is_default_and_fails_closed_without_endpoint(monkeypatch):
    monkeypatch.delenv("TOKENGOV_POLICY_SOURCE", raising=False)
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    with pytest.raises(PolicyLoadError, match="AZURE_APPCONFIG_ENDPOINT"):
        load_policy_from_environment()


@pytest.fixture
def azure_policy_runtime(monkeypatch):
    import azure.appconfiguration
    import azure.identity

    monkeypatch.setenv("TOKENGOV_POLICY_SOURCE", "azure")
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://policy-test.azconfig.io")
    monkeypatch.setenv("TOKENGOV_POLICY_KEY", "tokengov:policy")
    monkeypatch.setenv("TOKENGOV_POLICY_LABEL", "approved-v1")
    for key in ("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", "CONTAINER_APP_NAME", "WEBSITE_INSTANCE_ID"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    default, cli = object(), object()

    def default_credential(**kwargs):
        calls.append(("default", kwargs))
        return default

    def cli_credential(**kwargs):
        calls.append(("cli", kwargs))
        return cli

    def get_setting(**kwargs):
        calls.append(("get", kwargs))
        return SimpleNamespace(value=json.dumps(_policy()), content_type="application/json",
                               last_modified=None, etag="unchanged-etag")

    def client(endpoint, credential):
        calls.append(("client", endpoint, credential))
        return SimpleNamespace(get_configuration_setting=get_setting)

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", default_credential)
    monkeypatch.setattr(azure.identity, "AzureCliCredential", cli_credential)
    monkeypatch.setattr(azure.appconfiguration, "AzureAppConfigurationClient", client)
    return calls, default, cli


def test_explicit_policy_subscription_pins_cli_without_changing_authority(azure_policy_runtime, monkeypatch):
    calls, _, cli = azure_policy_runtime
    subscription = "a91cc1ba-bd19-43a7-90ea-120794c0fbc6"
    monkeypatch.setenv("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", subscription)
    loaded = load_policy_from_environment()
    assert calls == [
        ("cli", {"subscription": subscription, "process_timeout": 20}),
        ("client", "https://policy-test.azconfig.io", cli),
        ("get", {"key": "tokengov:policy", "label": "approved-v1"}),
    ]
    assert loaded.document == _policy()
    assert loaded.provenance["source"] == "azure_app_configuration"
    assert loaded.provenance["etag"] == "unchanged-etag"
    assert not loaded.provenance.get("development_only")


@pytest.mark.parametrize("host", [None, "CONTAINER_APP_NAME", "WEBSITE_INSTANCE_ID"])
def test_unpinned_policy_preserves_default_runtime_identity(azure_policy_runtime, monkeypatch, host):
    calls, default, _ = azure_policy_runtime
    if host:
        monkeypatch.setenv(host, "hosted-runtime")
    assert load_policy_from_environment().provenance["source"] == "azure_app_configuration"
    assert calls[0] == ("default", {})
    assert calls[1] == ("client", "https://policy-test.azconfig.io", default)


@pytest.mark.parametrize("host", ["CONTAINER_APP_NAME", "WEBSITE_INSTANCE_ID"])
def test_hosted_policy_rejects_local_cli_override(azure_policy_runtime, monkeypatch, host):
    calls, _, _ = azure_policy_runtime
    monkeypatch.setenv(host, "hosted-runtime")
    monkeypatch.setenv("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", "a91cc1ba-bd19-43a7-90ea-120794c0fbc6")
    with pytest.raises(PolicyLoadError, match="local-development only"):
        load_policy_from_environment()
    assert not calls


@pytest.mark.parametrize("subscription", ["wrong-subscription", " ", "--tenant elsewhere"])
def test_policy_rejects_invalid_subscription_without_fallback(azure_policy_runtime, monkeypatch, subscription):
    calls, _, _ = azure_policy_runtime
    monkeypatch.setenv("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", subscription)
    with pytest.raises(PolicyLoadError, match="subscription UUID"):
        load_policy_from_environment()
    assert not calls


def test_pinned_auth_failure_does_not_try_default_identity_or_local_policy(azure_policy_runtime, monkeypatch, tmp_path):
    import azure.identity
    from azure.core.exceptions import ClientAuthenticationError

    calls, _, _ = azure_policy_runtime
    path = tmp_path / "fallback.json"
    path.write_text(json.dumps(_policy()), encoding="utf-8")
    monkeypatch.setenv("TOKENGOV_POLICY_FILE", str(path))
    monkeypatch.setenv("TOKENGOV_POLICY_AZURE_CLI_SUBSCRIPTION", "a91cc1ba-bd19-43a7-90ea-120794c0fbc6")

    def fail(**kwargs):
        raise ClientAuthenticationError("Selected account requires login")

    monkeypatch.setattr(azure.identity, "AzureCliCredential", fail)
    with pytest.raises(PolicyLoadError, match="Selected account requires login"):
        load_policy_from_environment()
    assert not calls


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


def test_admission_matches_explicit_deployment_and_catalog_model_aliases():
    from hashlib import sha256

    snapshot = {
        "report_id": "RPT-1",
        "plan_id": "plan-1",
        "schema_version": "1.0",
        "created_at": "2026-07-20T00:00:00+00:00",
        "description": "RAG workload",
        "intake": {"model": "gpt-5.6-luna"},
        "prediction": {
            "provider": "azure_openai",
            "model": "gpt-5.6-luna",
            "pricing_verified": True,
            "cost_per_call": {"mean": 0.013},
        },
        "infrastructure": {"status": "not_estimated"},
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    receipt = {**snapshot, "receipt_id": "receipt-1", "content_hash": sha256(canonical.encode()).hexdigest()}
    policy = _policy()
    policy["admission"]["allowed_models"] = ["gpt-5-6-luna"]

    decision = admit_receipt(
        receipt,
        LoadedPolicy(policy, {"source": "azure_app_configuration"}),
    )

    assert decision["status"] == "admitted"


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
            {"schema_version": "7.0"},
            LoadedPolicy(_policy(), {"source": "azure_app_configuration"}),
        )


def test_policy_validates_versioned_infrastructure_coverage():
    policy = _policy()
    policy["infrastructure_coverage"] = {
        "schema_version": "infrastructure-coverage-policy.v1",
        "applicable_routes": ["foundry"],
        "require_confirmed_estimate": True,
        "min_priced_coverage_ratio": 1.0,
        "allow_material_unpriced_items": False,
        "required_price_type": "Consumption",
        "currency": "USD",
        "require_exact_meter_match": True,
        "max_price_evidence_age_days": 30,
        "allowed_regions": ["eastus"],
        "required_safeguards": ["managed_identity"],
        "max_monthly_cost_usd": 500,
    }

    assert validate_policy(policy)["infrastructure_coverage"]["allowed_regions"] == [
        "eastus"
    ]
    policy["infrastructure_coverage"]["min_priced_coverage_ratio"] = 1.1
    with pytest.raises(PolicyLoadError, match="min_priced_coverage_ratio"):
        validate_policy(policy)


def test_schema_six_admission_enforces_infrastructure_coverage():
    from datetime import datetime, timezone
    from hashlib import sha256

    policy = _policy()
    policy["infrastructure_coverage"] = {
        "schema_version": "infrastructure-coverage-policy.v1",
        "applicable_routes": ["foundry"],
        "require_confirmed_estimate": True,
        "min_priced_coverage_ratio": 1.0,
        "allow_material_unpriced_items": False,
        "required_price_type": "Consumption",
        "currency": "USD",
        "require_exact_meter_match": True,
        "max_price_evidence_age_days": 30,
        "allowed_regions": ["eastus"],
        "required_safeguards": ["managed_identity"],
        "max_monthly_cost_usd": 500,
    }
    infrastructure = {
        "schema_version": "infrastructure-forecast.v1",
        "status": "estimated",
        "route_id": "foundry",
        "region": "eastus",
        "classification": "modeled",
        "confirmed": True,
        "architecture": {
            "safeguards": [
                {"safeguard_id": "managed_identity", "status": "design_satisfied"}
            ]
        },
        "price_snapshot": {
            "price_type": "Consumption",
            "currency": "USD",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lines": [{
                "evidence_status": "sourced",
                "meter_id": "meter-1",
                "meter_name": "Runtime",
            }],
        },
        "priced_subtotal_usd": 100,
        "coverage_ratio": 1.0,
        "unpriced_material_items": [],
    }
    snapshot = {
        "report_id": "RPT-6",
        "plan_id": "plan-6",
        "schema_version": "6.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "Foundry workload",
        "intake": {},
        "analysis": {},
        "confirmed_profile": {},
        "assumptions": [],
        "clarifications": [],
        "exclusions": [],
        "route": {"route_id": "foundry"},
        "commercial": {},
        "purchase": None,
        "token_subforecast": None,
        "hybrid": None,
        "acceptance_assumption": None,
        "meter_stack": {},
        "trajectory_contract": {},
        "prediction": {
            "provider": "azure_openai",
            "model": "gpt-4.1",
            "pricing_verified": True,
            "cost_per_call": {"mean": 0.01},
        },
        "infrastructure": infrastructure,
    }
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    receipt = {
        **snapshot,
        "receipt_id": "receipt-6",
        "content_hash": sha256(canonical.encode()).hexdigest(),
    }

    decision = admit_receipt(
        receipt, LoadedPolicy(policy, {"source": "azure_app_configuration"})
    )

    assert decision["status"] == "admitted"
    assert all(check["passed"] for check in decision["checks"])
    blocked = json.loads(json.dumps(receipt))
    blocked["infrastructure"]["confirmed"] = False
    blocked_snapshot = {
        key: value
        for key, value in blocked.items()
        if key not in {"receipt_id", "content_hash"}
    }
    blocked["content_hash"] = sha256(
        json.dumps(
            blocked_snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    assert admit_receipt(
        blocked, LoadedPolicy(policy, {"source": "azure_app_configuration"})
    )["status"] == "rejected"


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


def test_policy_change_normalizes_null_supersedes_and_rejects_unknown_draft(tmp_path):
    loaded = LoadedPolicy(
        _policy(), {"source": "azure_app_configuration", "etag": "etag-1"}
    )
    store = PolicyChangeStore(tmp_path)
    payload = {
        "supersedes_change_id": None,
        "reason": "Create a new policy revision.",
        "proposed_version": "2026-07-20.2",
        "changes": {"execution.routing_mode": "cost"},
    }

    proposal = store.create(payload, loaded)

    assert proposal["supersedes_change_id"] is None
    payload["supersedes_change_id"] = "PCR-0000000000"
    payload["proposed_version"] = "2026-07-20.3"
    with pytest.raises(ValueError, match="superseded policy draft was not found"):
        store.create(payload, loaded)


def test_policy_can_be_authored_from_conservative_defaults_and_superseded(tmp_path):
    active = _policy()
    loaded = LoadedPolicy(active, {"source": "azure_app_configuration", "etag": "etag-1"})
    store = PolicyChangeStore(tmp_path)

    created = store.create(
        {
            "authoring_mode": "create",
            "reason": "Author a replacement from conservative defaults.",
            "proposed_version": "2026-07-20.2",
            "changes": {
                "admission.allowed_models": ["gpt-4.1"],
                "execution.routing_mode": "quality",
            },
        },
        loaded,
    )
    revised = store.create(
        {
            "authoring_mode": "create",
            "supersedes_change_id": created["change_id"],
            "reason": "Raise the reviewed segment floor.",
            "proposed_version": "2026-07-20.2",
            "changes": {
                "admission.allowed_models": ["gpt-4.1"],
                "execution.routing_mode": "quality",
                "execution.evaluation.min_quality": 0.9,
            },
        },
        loaded,
    )

    assert created["proposed_policy"]["execution"]["budget"]["hard_cap_action"] == "deny"
    assert created["proposed_policy"]["execution"]["semantic_cache"]["enabled"] is False
    assert revised["supersedes_change_id"] == created["change_id"]
    assert revised["proposed_policy"]["execution"]["evaluation"]["min_quality"] == 0.9
    assert len(store.list()) == 2


def test_draft_delete_and_pending_retirement_are_append_only(tmp_path):
    loaded = LoadedPolicy(
        _policy(), {"source": "azure_app_configuration", "etag": "etag-1"}
    )
    store = PolicyChangeStore(tmp_path)
    deleted_proposal = store.create(
        {
            "reason": "Discard this local draft.",
            "proposed_version": "2026-07-20.2",
            "changes": {"execution.routing_mode": "cost"},
        },
        loaded,
    )
    pending_proposal = store.create(
        {
            "reason": "Retain this pending review.",
            "proposed_version": "2026-07-20.3",
            "changes": {"execution.routing_mode": "cost"},
        },
        loaded,
    )
    store.record_review(
        pending_proposal["change_id"],
        {
            "provider": "github",
            "pull_request_number": 42,
            "pull_request_url": "https://github.com/example/repo/pull/42",
        },
    )

    deleted = store.delete_draft(deleted_proposal["change_id"])

    assert deleted["status"] == "deleted"
    assert deleted["events"][-1]["event_id"].startswith("policy-delete-")
    assert [item["change_id"] for item in store.list()] == [
        pending_proposal["change_id"]
    ]
    assert (tmp_path / f"{deleted_proposal['change_id']}.json").is_file()
    with pytest.raises(ValueError, match="only a draft policy can be deleted"):
        store.delete_draft(pending_proposal["change_id"])

    retired = store.retire_pending(pending_proposal["change_id"])

    assert retired["status"] == "retired"
    assert retired["events"][-1]["event_id"].startswith("policy-retire-")
    assert retired["events"][-1]["reason"] == "removed_from_active_workspace"
    assert store.list() == []
    assert (tmp_path / f"{pending_proposal['change_id']}.json").is_file()
    with pytest.raises(ValueError, match="only a pending policy request can be retired"):
        store.retire_pending(deleted_proposal["change_id"])


def test_policy_review_events_are_append_only_and_active_requires_azure_match(tmp_path):
    loaded = LoadedPolicy(
        _policy(), {"source": "azure_app_configuration", "etag": "etag-1"}
    )
    store = PolicyChangeStore(tmp_path)
    proposal = store.create(
        {
            "reason": "Create a reviewed revision.",
            "proposed_version": "2026-07-20.2",
            "changes": {"execution.routing_mode": "cost"},
        },
        loaded,
    )

    pending = store.record_review(
        proposal["change_id"],
        {
            "provider": "github",
            "pull_request_number": 42,
            "pull_request_url": "https://github.com/example/repo/pull/42",
        },
    )

    assert pending["status"] == "pending"
    assert len(pending["events"]) == 1
    assert list((tmp_path / "events" / proposal["change_id"]).glob("*.json"))
    active = store.get(
        proposal["change_id"], active_policy=proposal["proposed_policy"]
    )
    assert active["status"] == "active"


def test_external_publication_resolves_draft_without_rewriting_history(tmp_path):
    from copy import deepcopy

    store = PolicyChangeStore(tmp_path)
    proposal = store.create({
        "reason": "Reviewed outside Studio.",
        "proposed_version": "2026-07-20.2",
        "changes": {"execution.routing_mode": "cost"},
    }, LoadedPolicy(_policy(), {"source": "azure_app_configuration", "etag": "base"}))
    path = tmp_path / f"{proposal['change_id']}.json"
    original = path.read_bytes()
    assert store.get(proposal["change_id"])["status"] == "draft"
    mismatch = deepcopy(proposal["proposed_policy"])
    mismatch["execution"]["budget"]["per_tenant_usd_per_run"] += 1
    assert store.get(proposal["change_id"], active_policy=mismatch)["status"] == "draft"
    assert store.get(proposal["change_id"], active_policy=proposal["proposed_policy"])["status"] == "active"
    assert path.read_bytes() == original
    assert not store._events(proposal["change_id"])
    store.delete_draft(proposal["change_id"])
    assert store.get(proposal["change_id"], active_policy=proposal["proposed_policy"])["status"] == "deleted"