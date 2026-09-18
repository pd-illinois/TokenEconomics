import copy
from datetime import datetime, timezone

import pytest

from costgov.policy_changes import PolicyChangeStore
from costgov.policy_publication import build_publication_preview
from costgov.policy_store import (
    LoadedPolicy, PolicyLoadError, measurement_authorization, validate_policy,
)
from test_policy_store import _policy


def measurement():
    return {
        "schema_version": "workload-measurement-policy.v1",
        "mode": "measurement_only",
        "workload_scope": "studio_batches",
        "max_questions": 10,
        "max_output_tokens": 1024,
        "max_elapsed_seconds": 300,
        "observed_model_cost_stop_usd": 0.25,
        "expires_at": "2026-09-10T22:00:00Z",
        "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False,
        "operational_promotion": False,
    }


def campaign_measurement():
    return {
        "schema_version": "workload-measurement-policy.v2",
        "mode": "measurement_only",
        "workload_scope": "studio_campaigns",
        "max_questions": 25,
        "max_output_tokens": 1024,
        "max_elapsed_seconds": 600,
        "observed_model_cost_stop_usd": 0.25,
        "max_campaign_repetitions": 100,
        "max_campaign_questions": 2500,
        "max_evaluation_runs": 100,
        "max_evaluation_rows_per_run": 25,
        "campaign_observed_model_cost_stop_usd": 25,
        "require_explicit_campaign_id": True,
        "expires_at": "2026-10-17T15:09:21.310+00:00",
        "acknowledge_incomplete_costs": True,
        "hard_spend_cap_guaranteed": False,
        "operational_promotion": False,
    }


def authority():
    document = _policy()
    document["execution"]["budget"]["hard_cap_action"] = "deny"
    document["measurement"] = measurement()
    return LoadedPolicy(document, {
        "source": "azure_app_configuration",
        "endpoint": "https://test.azconfig.io",
        "key": "tokengov:policy", "label": "reviewed", "etag": "exact-etag",
    })


NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_explicit_scope_does_not_relax_operational_policy():
    loaded = authority()
    before = copy.deepcopy(loaded.document)
    assert measurement_authorization(loaded, now=NOW) == measurement()
    assert loaded.document == before
    assert loaded.document["execution"]["budget"] == {
        "per_tenant_usd_per_run": 5.0, "hard_cap_action": "deny",
    }


def test_campaign_measurement_contract_is_bounded_and_authorizable():
    loaded = authority()
    loaded.document["measurement"] = campaign_measurement()
    assert measurement_authorization(loaded, now=NOW) == campaign_measurement()


@pytest.mark.parametrize("key,value", [
    ("max_questions", 26),
    ("max_campaign_repetitions", 101),
    ("max_campaign_questions", 2501),
    ("max_evaluation_runs", 101),
    ("max_evaluation_rows_per_run", 26),
    ("campaign_observed_model_cost_stop_usd", 25.01),
    ("require_explicit_campaign_id", False),
])
def test_invalid_campaign_measurement_contract_fails_closed(key, value):
    loaded = authority()
    loaded.document["measurement"] = campaign_measurement()
    loaded.document["measurement"][key] = value
    with pytest.raises(PolicyLoadError):
        validate_policy(loaded.document)


@pytest.mark.parametrize("key,value", [
    ("max_questions", True), ("max_questions", 11), ("max_questions", 0),
    ("max_output_tokens", 4097), ("max_elapsed_seconds", 601),
    ("observed_model_cost_stop_usd", float("nan")),
    ("observed_model_cost_stop_usd", float("inf")),
    ("observed_model_cost_stop_usd", 10**1000),
    ("observed_model_cost_stop_usd", True),
    ("observed_model_cost_stop_usd", 0),
    ("hard_spend_cap_guaranteed", True),
    ("operational_promotion", True),
    ("acknowledge_incomplete_costs", 1),
    ("workload_scope", "production"), ("mode", "enforce"),
    ("expires_at", "2026-09-10T22:00:00"),
    ("expires_at", "2026-09-10T22:00:00+01:00"),
    ("expires_at", "2026-09-10T22:00:00+0000"),
    ("expires_at", "2026-09-10 22:00:00Z"),
    ("expires_at", "invalid"), ("expires_at", None),
    ("extra", "not-allowed"),
])
def test_invalid_measurement_contract_fails_closed(key, value):
    loaded = authority()
    loaded.document["measurement"][key] = value
    with pytest.raises(PolicyLoadError):
        validate_policy(loaded.document)
    with pytest.raises(PolicyLoadError):
        measurement_authorization(loaded, now=NOW)


def test_historical_expired_policy_remains_valid_but_cannot_authorize():
    loaded = authority()
    loaded.document["measurement"]["expires_at"] = NOW.isoformat()
    assert validate_policy(loaded.document) == loaded.document
    with pytest.raises(PolicyLoadError, match="expired"):
        measurement_authorization(loaded, now=NOW)


@pytest.mark.parametrize("change", [
    lambda loaded: loaded.document.pop("measurement"),
    lambda loaded: loaded.document.update(status="draft"),
    lambda loaded: loaded.provenance.update(source="local_file"),
    lambda loaded: loaded.provenance.update(etag=""),
    lambda loaded: loaded.provenance.update(label=None),
    lambda loaded: loaded.provenance.update(development_only=True),
])
def test_missing_or_local_authority_never_enables_measurement(change):
    loaded = authority()
    change(loaded)
    with pytest.raises(PolicyLoadError):
        measurement_authorization(loaded, now=NOW)


def test_measurement_proposal_uses_existing_review_boundary(tmp_path):
    loaded = authority()
    loaded.document.pop("measurement")
    original = copy.deepcopy(loaded.document)
    proposal = PolicyChangeStore(tmp_path).create({
        "reason": "Explicitly reviewed bounded evidence collection; no hard-spend guarantee.",
        "proposed_version": "2026-09-08.measurement.1",
        "changes": {"measurement": measurement()},
    }, loaded)
    assert proposal["status"] == "draft"
    assert proposal["publication"]["azure_write_permitted"] is False
    assert loaded.document == original
    target = proposal["proposed_policy"]
    assert target["execution"] == original["execution"]
    preview = build_publication_preview(
        current_policy=original, current_etag="exact-etag",
        target_policy=target, expected_etag="exact-etag", action="publish",
    )
    assert set(preview["changed_fields"]) == {"version", "measurement"}
    assert preview["mutation_performed"] is False
    with pytest.raises(ValueError, match="ETag"):
        build_publication_preview(
            current_policy=original, current_etag="changed",
            target_policy=target, expected_etag="exact-etag", action="publish",
        )
