from __future__ import annotations

import pytest

from costgov.commercial_contracts import EvidenceStatus
from costgov.consumption_models import (
    CATALOG_VERSION,
    ConsumptionFamily,
    MeterLayer,
    ProductMeterStack,
    consumption_catalog,
    meter_stack_for,
)


def test_catalog_exposes_each_supported_experience_with_versioned_evidence():
    catalog = consumption_catalog()
    routes = {experience["route_id"] for experience in catalog["experiences"]}

    assert catalog["catalog_version"] == CATALOG_VERSION
    assert catalog["evidence_status"] == EvidenceStatus.DOCUMENTED.value
    assert routes == {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "foundry",
        "github_copilot",
        "copilot_studio_byom",
        "foundry_work_iq",
    }
    assert all(experience["version"] == CATALOG_VERSION for experience in catalog["experiences"])


def test_meter_stacks_keep_microsoft_and_github_credit_currencies_separate():
    microsoft = meter_stack_for("copilot_studio")
    github = meter_stack_for("github_copilot")

    assert {
        layer["currency"]
        for layer in microsoft["layers"]
        if layer["family"] == ConsumptionFamily.NATIVE_CREDIT.value
    } == {"Microsoft Copilot Credits"}
    assert {
        layer["currency"]
        for layer in github["layers"]
        if layer["family"] == ConsumptionFamily.TOKEN_DERIVED_CREDIT.value
    } == {"GitHub AI Credits"}


def test_meter_stack_rejects_duplicate_layers():
    layer = MeterLayer(
        layer_id="duplicate",
        family=ConsumptionFamily.SUBSCRIPTION,
        unit="user_month",
        currency="USD",
        authority="Test authority",
        source_url="https://example.com",
    )

    with pytest.raises(ValueError, match="duplicate meter layer"):
        ProductMeterStack(
            route_id="test",
            experience="test",
            display_name="Test",
            description="Test stack",
            version="test.v1",
            layers=(layer, layer),
        )


def test_unknown_consumption_route_fails_closed():
    with pytest.raises(ValueError, match="unsupported consumption route"):
        meter_stack_for("unknown")
