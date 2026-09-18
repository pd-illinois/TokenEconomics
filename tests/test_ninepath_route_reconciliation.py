from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from costgov.consumption_models import ConsumptionFamily
from costgov.route_reconciliation import (
    ApprovedAllocation,
    CompositeReconciliationStore,
    ForecastQuantity,
    PurchaseModel,
    SourceActualImportStore,
    build_composite_reconciliation,
    build_source_actual_import,
)

ROOT = Path(__file__).resolve().parents[1]


def _observation(
    observation_id: str,
    route: str,
    leg: str,
    family: ConsumptionFamily,
    meter_id: str,
    unit: str,
    purchase: PurchaseModel,
    number: int,
) -> dict:
    return {
        "observation_id": observation_id,
        "route_id": route,
        "authority": "source_authority",
        "target_leg": leg,
        "meter_family": family.value,
        "meter_id": meter_id,
        "native_unit": unit,
        "native_currency": unit,
        "quantity": float(number),
        "purchase_model": purchase.value,
        "source_row_hash": f"{number:x}" * 64,
        "actual_cost": float(number),
        "cost_currency": "USD",
    }


def _source_imports() -> list[dict]:
    m365 = build_source_actual_import(
        source="m365_cost_management",
        import_id="m365-1",
        source_revision="api.v1",
        source_export_hash="a" * 64,
        billing_period="2026-09",
        refreshed_at="2026-09-02T12:00:00+00:00",
        finalization_state="finalized",
        finalized_at="2026-09-03T12:00:00+00:00",
        observations=[
            _observation(
                "seat",
                "included",
                "subscription",
                ConsumptionFamily.SUBSCRIPTION,
                "seat",
                "seat",
                PurchaseModel.SEAT,
                1,
            ),
            _observation(
                "pack",
                "copilot_studio_byom",
                "commercial",
                ConsumptionFamily.NATIVE_CREDIT,
                "studio-credit",
                "credit",
                PurchaseModel.PACK,
                2,
            ),
            _observation(
                "p3",
                "cowork",
                "commercial",
                ConsumptionFamily.NATIVE_CREDIT,
                "cowork-credit",
                "credit",
                PurchaseModel.P3,
                3,
            ),
            _observation(
                "payg",
                "work_iq",
                "work_iq",
                ConsumptionFamily.NATIVE_CREDIT,
                "work-iq-credit",
                "credit",
                PurchaseModel.PAYG,
                4,
            ),
        ],
    )
    power = build_source_actual_import(
        source="power_platform",
        import_id="power-1",
        source_revision="admin.v1",
        source_export_hash="b" * 64,
        billing_period="2026-09",
        refreshed_at="2026-09-02T12:00:00+00:00",
        finalization_state="provisional",
        observations=[
            _observation(
                "power-pack-unattributed",
                "copilot_studio",
                "commercial",
                ConsumptionFamily.NATIVE_CREDIT,
                "unmatched-studio-credit",
                "credit",
                PurchaseModel.PACK,
                5,
            )
        ],
    )
    github = build_source_actual_import(
        source="github_billing",
        import_id="github-1",
        source_revision="billing.v1",
        source_export_hash="c" * 64,
        billing_period="2026-09",
        refreshed_at="2026-09-02T12:00:00+00:00",
        finalization_state="finalized",
        finalized_at="2026-09-03T12:00:00+00:00",
        observations=[
            _observation(
                "github-allowance",
                "github_copilot",
                "github_copilot",
                ConsumptionFamily.TOKEN_DERIVED_CREDIT,
                "github-credit",
                "github_ai_credit",
                PurchaseModel.GITHUB_ALLOWANCE,
                6,
            )
        ],
    )
    azure = build_source_actual_import(
        source="azure_billing",
        import_id="azure-1",
        source_revision="actual-cost.v1",
        source_export_hash="d" * 64,
        billing_period="2026-09",
        refreshed_at="2026-09-02T12:00:00+00:00",
        finalization_state="finalized",
        finalized_at="2026-09-03T12:00:00+00:00",
        observations=[
            _observation(
                "azure-foundry",
                "copilot_studio_byom",
                "foundry",
                ConsumptionFamily.DIRECT_TOKEN,
                "foundry-token",
                "model_token",
                PurchaseModel.AZURE_COST,
                7,
            )
        ],
    )
    return [m365, power, github, azure]


def _forecast(
    line_id: str,
    route: str,
    leg: str,
    family: ConsumptionFamily,
    meter_id: str,
    unit: str,
    purchase: PurchaseModel,
    number: int,
) -> ForecastQuantity:
    return ForecastQuantity(
        forecast_line_id=line_id,
        forecast_evidence_hash=f"{number + 8:x}" * 64,
        route_id=route,
        target_leg=leg,
        meter_family=family,
        meter_id=meter_id,
        native_unit=unit,
        native_currency=unit,
        purchase_model=purchase,
        billing_period="2026-09",
        quantity=float(number - 1),
    )


def _build() -> dict:
    forecasts = [
        _forecast(
            "f-seat", "included", "subscription", ConsumptionFamily.SUBSCRIPTION,
            "seat", "seat", PurchaseModel.SEAT, 1
        ),
        _forecast(
            "f-pack", "copilot_studio_byom", "commercial",
            ConsumptionFamily.NATIVE_CREDIT, "studio-credit", "credit",
            PurchaseModel.PACK, 2
        ),
        _forecast(
            "f-p3", "cowork", "commercial", ConsumptionFamily.NATIVE_CREDIT,
            "cowork-credit", "credit", PurchaseModel.P3, 3
        ),
        _forecast(
            "f-payg", "work_iq", "work_iq", ConsumptionFamily.NATIVE_CREDIT,
            "work-iq-credit", "credit", PurchaseModel.PAYG, 4
        ),
        _forecast(
            "f-github", "github_copilot", "github_copilot",
            ConsumptionFamily.TOKEN_DERIVED_CREDIT, "github-credit",
            "github_ai_credit", PurchaseModel.GITHUB_ALLOWANCE, 6
        ),
        _forecast(
            "f-azure", "copilot_studio_byom", "foundry",
            ConsumptionFamily.DIRECT_TOKEN, "foundry-token", "model_token",
            PurchaseModel.AZURE_COST, 7
        ),
    ]
    allocations = [
        ApprovedAllocation(
            allocation_id=f"allocation-{observation_id}",
            observation_id=observation_id,
            allocation_revision="allocation.v1",
            allocation_content_hash=f"{index:x}" * 64,
            allocation_basis="approved_contract_or_commitment",
            allocated_cost_usd=float(index),
        )
        for index, observation_id in enumerate(
            ["seat", "pack", "p3", "payg", "github-allowance", "azure-foundry"],
            start=1,
        )
    ]
    return build_composite_reconciliation(
        reconciliation_scope_id="scope-1",
        forecasts=forecasts,
        source_imports=_source_imports(),
        approved_allocations=allocations,
        created_at="2026-09-04T12:00:00+00:00",
    )


def test_composite_reconciles_native_quantities_before_money_and_keeps_sources() -> None:
    result = _build()

    assert result["reconciliation_order"] == [
        "native_quantity_variance",
        "approved_monetary_allocation",
    ]
    assert len(result["source_imports"]) == 4
    assert {item["finalization_state"] for item in result["source_imports"]} == {
        "provisional",
        "finalized",
    }
    assert len(result["native_variances"]) == 6
    assert all(item["forecast_evidence_hash"] for item in result["native_variances"])
    assert all(item["actual_evidence_hashes"] for item in result["native_variances"])
    assert {item["target_leg"] for item in result["native_variances"] if item["route_id"] == "copilot_studio_byom"} == {
        "commercial",
        "foundry",
    }


def test_purchase_sources_and_unattributed_totals_remain_separate() -> None:
    result = _build()

    assert [item["purchase_model"] for item in result["purchase_views"]] == [
        "seat",
        "pack",
        "p3",
        "payg",
        "github_allowance",
        "azure_cost",
    ]
    assert result["unattributed"] == [
        {
            "observation_id": "power-pack-unattributed",
            "source_import_hash": result["source_imports"][1]["content_hash"],
            "observation_content_hash": result["unattributed"][0]["observation_content_hash"],
            "purchase_model": "pack",
            "quantity": 5.0,
            "native_unit": "credit",
            "actual_cost": 5.0,
            "cost_currency": "USD",
            "reason": "no_matching_forecast",
        }
    ]
    assert result["status"] == "provisional"


def test_composite_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    result = _build()
    store = CompositeReconciliationStore(tmp_path)
    first, created = store.append(result)
    second, created_again = CompositeReconciliationStore(tmp_path).append(result)

    assert created is True
    assert created_again is False
    assert first == second

    actual = _source_imports()[0]
    actual_first, actual_created = SourceActualImportStore(
        tmp_path / "actuals"
    ).append(actual)
    actual_second, actual_created_again = SourceActualImportStore(
        tmp_path / "actuals"
    ).append(actual)
    assert actual_first == actual_second
    assert actual_created is True
    assert actual_created_again is False


def test_actual_and_composite_outputs_match_published_schemas() -> None:
    actual_schema = json.loads(
        (ROOT / "data/contracts/source-actual-import.v1.schema.json").read_text()
    )
    composite_schema = json.loads(
        (
            ROOT / "data/contracts/composite-route-reconciliation.v1.schema.json"
        ).read_text()
    )
    for actual_import in _source_imports():
        Draft202012Validator(actual_schema).validate(actual_import)
    Draft202012Validator(composite_schema).validate(_build())
