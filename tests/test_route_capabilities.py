from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from costgov.consumption_models import consumption_catalog
from costgov.route_capabilities import (
    ROUTE_CAPABILITY_PROFILES,
    ROUTE_CAPABILITY_SCHEMA_VERSION,
    EnforcementScope,
    RouteControl,
    route_capability_catalog,
    route_capability_profile_for,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 2)


def test_every_meter_stack_has_one_hash_bound_capability_profile() -> None:
    meter_routes = {
        item["route_id"] for item in consumption_catalog()["experiences"]
    }
    catalog = route_capability_catalog(as_of=AS_OF)
    profiles = catalog["profiles"]

    assert {item["route_id"] for item in profiles} == meter_routes
    assert len(profiles) == 9
    assert len({item["content_hash"] for item in profiles}) == 9
    assert all(item["meter_stack_content_hash"] for item in profiles)


def test_hybrid_profiles_bind_controls_to_independent_legs() -> None:
    studio_byom = route_capability_profile_for(
        "copilot_studio_byom", as_of=AS_OF
    )
    foundry_work_iq = route_capability_profile_for(
        "foundry_work_iq", as_of=AS_OF
    )

    assert {item["target_leg"] for item in studio_byom["controls"]} >= {
        "commercial",
        "foundry",
    }
    assert {item["target_leg"] for item in foundry_work_iq["controls"]} >= {
        "foundry",
        "work_iq",
    }


def test_external_products_do_not_claim_azure_runtime_authority() -> None:
    external_routes = {
        "included",
        "cowork",
        "agent_builder",
        "copilot_studio",
        "work_iq",
        "github_copilot",
    }
    for route_id in external_routes:
        profile = route_capability_profile_for(route_id, as_of=AS_OF)
        assert not any(
            item["authority"] == "azure_tokengov"
            for item in profile["controls"]
        )


def test_unsupported_authority_claim_fails_closed() -> None:
    with pytest.raises(ValueError, match="supported authority"):
        RouteControl(
            control_id="false-license-control",
            control_kind="entitlement",
            authority="azure_tokengov",
            capability="license_management",
            enforcement_scope=EnforcementScope.RUNTIME_ENFORCED,
            target_leg="commercial",
        )


def test_duplicate_controls_and_stale_profiles_fail_closed() -> None:
    profile = ROUTE_CAPABILITY_PROFILES[0]
    with pytest.raises(ValueError, match="control IDs"):
        replace(profile, controls=(profile.controls[0], profile.controls[0]))
    with pytest.raises(ValueError, match="requires review"):
        route_capability_profile_for("foundry", as_of=date(2026, 12, 2))
    with pytest.raises(ValueError, match="unsupported route"):
        route_capability_profile_for("unknown", as_of=AS_OF)


def test_route_capability_schema_matches_runtime_contract() -> None:
    schema = json.loads(
        (
            ROOT
            / "data"
            / "contracts"
            / "route-capability-profile.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"]["const"] == (
        ROUTE_CAPABILITY_SCHEMA_VERSION
    )
    assert set(schema["$defs"]["control"]["properties"]["enforcement_scope"]["enum"]) == {
        item.value for item in EnforcementScope
    }
