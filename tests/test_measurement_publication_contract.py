import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from costgov.policy_publication import build_publication_preview
from costgov.policy_store import LoadedPolicy, PolicyLoadError, measurement_authorization, validate_policy


ROOT = Path(__file__).resolve().parents[1]


def target():
    return json.loads((ROOT / "data" / "policies" /
                       "tokengov-te003-live-proof.2026-09-09.measurement.1.json").read_text())


def loaded():
    return LoadedPolicy(target(), {
        "source": "azure_app_configuration", "endpoint": "https://test.azconfig.io",
        "key": "tokengov:policy", "label": "reviewed", "etag": "exact",
    })


def test_review_changes_only_measurement_and_version():
    proposed = target()
    base = json.loads((ROOT / "data" / "policies" /
                      "tokengov-te003-live-proof.2026-08-31.2.json").read_text())
    preview = build_publication_preview(
        current_policy=base, target_policy=proposed,
        current_etag="exact", expected_etag="exact", action="publish",
    )
    assert set(preview["changed_fields"]) == {"version", "measurement"}
    assert not preview["mutation_performed"]
    assert proposed["execution"] == base["execution"]
    assert proposed["admission"] == base["admission"]


def test_azure_authorization_is_expiring_and_read_only():
    policy = loaded()
    before = copy.deepcopy(policy)
    grant = measurement_authorization(policy, now=datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert grant["max_questions"] == 10
    assert grant["hard_spend_cap_guaranteed"] is False
    assert policy == before
    assert validate_policy(policy.document)
    with pytest.raises(PolicyLoadError, match="expired"):
        measurement_authorization(policy, now=datetime(2026, 9, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("key,value", [
    ("max_questions", 11), ("max_output_tokens", True),
    ("max_elapsed_seconds", 601), ("observed_model_cost_stop_usd", float("nan")),
    ("operational_promotion", True), ("hard_spend_cap_guaranteed", True),
    ("expires_at", "2026-09-11T13:00:00"), ("extra", "not allowed"),
])
def test_invalid_measurement_cannot_publish(key, value):
    proposed = target()
    proposed["measurement"][key] = value
    with pytest.raises(PolicyLoadError):
        validate_policy(proposed)


@pytest.mark.parametrize("change", [
    lambda policy: policy.document.pop("measurement"),
    lambda policy: policy.provenance.update(source="local_file"),
    lambda policy: policy.provenance.update(etag=""),
    lambda policy: policy.provenance.update(development_only=True),
])
def test_absent_or_local_grant_cannot_authorize(change):
    policy = loaded()
    change(policy)
    with pytest.raises(PolicyLoadError):
        measurement_authorization(policy)
