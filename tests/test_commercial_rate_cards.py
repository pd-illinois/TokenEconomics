from __future__ import annotations

import json
from datetime import date

import pytest

from costgov.commercial_contracts import (
    BillingDisposition,
    EntitlementContext,
    EvidenceStatus,
    NativeUnit,
)
from costgov.commercial_rate_cards import RateCardError, load_copilot_studio_rate_card


def test_copilot_studio_rate_card_preserves_documented_native_credit_units():
    card = load_copilot_studio_rate_card(as_of=date(2026, 8, 20))

    assert card.rate_card_id == "microsoft-copilot-studio-standard-harness"
    assert card.version == "2026-08-03.1"
    assert card.evidence_status is EvidenceStatus.DOCUMENTED
    assert card.source_url == (
        "https://learn.microsoft.com/en-us/microsoft-copilot-studio/"
        "requirements-messages-management"
    )

    assert card.meter("classic_answer").credits_per_unit == 1
    assert card.meter("generative_answer").credits_per_unit == 2
    assert card.meter("agent_action").credits_per_unit == 5
    assert card.meter("tenant_graph_grounding").credits_per_unit == 10

    flow = card.meter("agent_flow_actions")
    assert flow.native_unit is NativeUnit.ACTION
    assert flow.unit_size == 100
    assert flow.credits_per_unit == 13

    premium = card.meter("premium_ai_tool")
    assert premium.native_unit is NativeUnit.TOKEN_OR_CHARACTER
    assert premium.unit_size == 1000
    assert premium.credits_per_unit == 10
    assert premium.separately_billed_byom is True


def test_entitlement_context_does_not_assume_b2e_usage_is_zero_rated():
    context = EntitlementContext(
        user_segment="employees",
        audience_type="b2e",
        authenticated=None,
        identity_mode="unknown",
        license_sku=None,
        channel="teams",
        trigger_type="interactive",
        product_boundary="copilot_studio",
        evidence_version="proposed-input.v1",
    )

    assert context.default_disposition is BillingDisposition.UNKNOWN_REQUIRES_POLICY


def test_rate_card_loader_fails_closed_on_missing_source(tmp_path):
    fixture = {
        "schema_version": "1.0",
        "rate_card_id": "invalid",
        "version": "1",
        "status": "active_reviewed",
        "evidence_status": "documented",
        "effective_from": "2026-08-03",
        "review_after": "2027-02-03",
        "source_retrieved_at": "2026-08-20T00:00:00Z",
        "meters": [],
    }
    path = tmp_path / "rate-card.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(RateCardError, match="source_url"):
        load_copilot_studio_rate_card(path, as_of=date(2026, 8, 20))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("credits_per_unit", -1, "credits_per_unit"),
        ("credits_per_unit", float("inf"), "credits_per_unit"),
        ("unit_size", 0, "unit_size"),
    ],
)
def test_rate_card_loader_rejects_invalid_meter_numbers(
    tmp_path, field, value, message
):
    fixture = {
        "schema_version": "1.0",
        "rate_card_id": "invalid",
        "version": "1",
        "status": "active_reviewed",
        "evidence_status": "documented",
        "product": "Test product",
        "experience": "test",
        "harness": "test",
        "effective_from": "2026-08-03",
        "review_after": "2027-02-03",
        "source_url": "https://learn.microsoft.com/test",
        "source_revision": "test-revision",
        "source_retrieved_at": "2026-08-20T00:00:00Z",
        "meters": [
            {
                "meter_id": "bad",
                "feature": "bad",
                "native_unit": "event",
                "unit_size": 1,
                "credits_per_unit": 1,
            }
        ],
    }
    fixture["meters"][0][field] = value
    path = tmp_path / "rate-card.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(RateCardError, match=message):
        load_copilot_studio_rate_card(path, as_of=date(2026, 8, 20))


def test_rate_card_loader_rejects_unreviewed_or_not_yet_effective_data(tmp_path):
    fixture = {
        "schema_version": "1.0",
        "rate_card_id": "future",
        "version": "1",
        "status": "draft",
        "evidence_status": "proposed",
        "effective_from": "2026-09-01",
        "review_after": "2027-02-03",
        "source_url": "https://learn.microsoft.com/test",
        "source_retrieved_at": "2026-08-20T00:00:00Z",
        "meters": [],
    }
    path = tmp_path / "rate-card.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(RateCardError, match="active_reviewed"):
        load_copilot_studio_rate_card(path, as_of=date(2026, 8, 20))

    fixture["status"] = "active_reviewed"
    fixture["evidence_status"] = "documented"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(RateCardError, match="not effective"):
        load_copilot_studio_rate_card(path, as_of=date(2026, 8, 20))


def test_rate_card_loader_rejects_stale_evidence():
    with pytest.raises(RateCardError, match="stale"):
        load_copilot_studio_rate_card(as_of=date(2027, 2, 4))
