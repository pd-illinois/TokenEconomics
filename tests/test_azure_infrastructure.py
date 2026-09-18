from __future__ import annotations

import copy

import pytest

from costgov.azure_infrastructure import (
    confirm_infrastructure_forecast,
    build_infrastructure_forecast,
    content_hash,
    route_requires_infrastructure,
    validate_infrastructure_forecast,
)


class FakeAzureMcp:
    def __init__(self, missing_meter: str | None = None):
        self.missing_meter = missing_meter

    def collect(self, architecture_intent, price_queries):
        responses = []
        for index, query in enumerate(price_queries):
            if query["meter_name"] == self.missing_meter:
                prices = []
            else:
                prices = [{
                    "armSkuName": query["sku_name"],
                    "skuName": query["sku_name"],
                    "productName": query["service_name"],
                    "serviceName": query["service_name"],
                    "serviceFamily": "Test",
                    "region": "eastus",
                    "retailPrice": 0.1 + index,
                    "unitPrice": 0.1 + index,
                    "currencyCode": "USD",
                    "unitOfMeasure": "1 Unit",
                    "priceType": "Consumption",
                    "effectiveStartDate": "2026-01-01T00:00:00+00:00",
                    "meterName": query["meter_name"],
                    "meterId": f"meter-{index}",
                }]
            responses.append({"status": 200, "results": {"prices": prices}})
        return {"status": 200, "results": {"designArchitecture": "guided state"}}, responses


class EffectiveDatedAzureMcp(FakeAzureMcp):
    def collect(self, architecture_intent, price_queries):
        guidance, responses = super().collect(architecture_intent, price_queries)
        for query, response in zip(price_queries, responses):
            if query["meter_name"] != "Analytics Logs Data Ingestion":
                continue
            current = response["results"]["prices"][0]
            response["results"]["prices"] = [
                {
                    **current,
                    "retailPrice": 0.0,
                    "unitPrice": 0.0,
                    "effectiveStartDate": "2020-01-01T00:00:00+00:00",
                },
                {
                    **current,
                    "retailPrice": 2.3,
                    "unitPrice": 2.3,
                    "effectiveStartDate": "2026-01-01T00:00:00+00:00",
                },
            ]
        return guidance, responses


class SameDatedAllowanceAzureMcp(EffectiveDatedAzureMcp):
    def collect(self, architecture_intent, price_queries):
        guidance, responses = super().collect(architecture_intent, price_queries)
        for query, response in zip(price_queries, responses):
            if query["meter_name"] == "Analytics Logs Data Ingestion":
                response["results"]["prices"][0]["effectiveStartDate"] = (
                    "2026-01-01T00:00:00+00:00"
                )
        return guidance, responses


def _parameters(route: str = "foundry"):
    return {
        "route": route,
        "azure_region": "eastus",
        "users": 100,
        "calls_per_user_per_day": 5,
        "confirmed_profile": {"agent_pattern": "react_agent"},
    }


def test_customer_hosted_routes_are_explicit():
    assert route_requires_infrastructure("foundry")
    assert route_requires_infrastructure("copilot_studio_byom")
    assert route_requires_infrastructure("foundry_work_iq")
    assert not route_requires_infrastructure("microsoft_copilot")


def test_rag_search_capacity_is_priced_with_explicit_baseline_quantity():
    parameters = _parameters()
    parameters["confirmed_profile"] = {"agent_pattern": "rag_pipeline"}
    forecast = build_infrastructure_forecast("Books RAG", parameters, transport=FakeAzureMcp())
    line = next(line for line in forecast["price_snapshot"]["lines"] if line["line_id"] == "rag_search_capacity")
    assert line["quantity"] == 2190
    assert line["service_name"] == "Azure Cognitive Search"
    assert line["meter_name"] == "Standard S1 Unit"
    assert forecast["coverage_ratio"] == 1
    assert forecast["status"] == "estimated"
    missing = build_infrastructure_forecast(
        "Books RAG", parameters, transport=FakeAzureMcp(missing_meter="Standard S1 Unit")
    )
    assert missing["status"] == "partial"
    assert "rag_search" in missing["unpriced_material_items"]


def test_builds_confirmable_sourced_forecast_with_deterministic_total():
    forecast = build_infrastructure_forecast(
        "Customer support agent", _parameters(), transport=FakeAzureMcp()
    )

    assert forecast["status"] == "estimated"
    assert forecast["priced_subtotal_usd"] > 0
    assert forecast["coverage_ratio"] == pytest.approx(1.0)
    assert forecast["unpriced_material_items"] == []
    assert forecast["architecture"]["classification"] == "proposed"
    assert forecast["price_snapshot"]["source"] == "Azure MCP pricing/pricing_get"
    assert forecast["confirmed"] is False
    confirmed = confirm_infrastructure_forecast(
        forecast, expected_hash=forecast["content_hash"]
    )
    assert confirmed["confirmed"] is True


def test_missing_meter_is_unpriced_not_zero():
    forecast = build_infrastructure_forecast(
        "Customer support agent",
        _parameters(),
        transport=FakeAzureMcp(missing_meter="Standard Requests"),
    )

    line = next(
        item
        for item in forecast["price_snapshot"]["lines"]
        if item["meter_name"] == "Standard Requests"
    )
    assert line["evidence_status"] == "unpriced"
    assert line["monthly_cost_usd"] is None
    assert forecast["status"] == "partial"
    assert forecast["unpriced_material_lines"] == ["runtime_requests"]


def test_latest_effective_meter_wins_over_obsolete_zero_row():
    forecast = build_infrastructure_forecast(
        "Customer support agent",
        _parameters(),
        transport=EffectiveDatedAzureMcp(),
    )

    line = next(
        item
        for item in forecast["price_snapshot"]["lines"]
        if item["line_id"] == "observability_ingestion"
    )
    assert line["retail_unit_price"] == pytest.approx(2.3)
    assert line["monthly_cost_usd"] == pytest.approx(11.5)


def test_same_meter_zero_allowance_uses_conservative_positive_rate():
    forecast = build_infrastructure_forecast(
        "Customer support agent",
        _parameters(),
        transport=SameDatedAllowanceAzureMcp(),
    )

    line = next(
        item
        for item in forecast["price_snapshot"]["lines"]
        if item["line_id"] == "observability_ingestion"
    )
    assert line["selection_rule"] == "conservative_positive_rate_same_meter_v1"
    assert line["excluded_zero_allowance_rows"] == 1
    assert line["allowance_application"] == "not_applied_without_versioned_quantity"


def test_saas_route_is_not_applicable():
    forecast = build_infrastructure_forecast(
        "Microsoft Copilot agent", _parameters("microsoft_copilot")
    )
    assert forecast["status"] == "not_applicable"


def test_forecast_validation_rejects_tampered_total_and_nan():
    forecast = build_infrastructure_forecast(
        "Customer support agent", _parameters(), transport=FakeAzureMcp()
    )
    tampered = copy.deepcopy(forecast)
    tampered.pop("content_hash")
    tampered["priced_subtotal_usd"] += 1
    with pytest.raises(ValueError, match="subtotal"):
        validate_infrastructure_forecast(tampered)

    tampered["priced_subtotal_usd"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        validate_infrastructure_forecast(tampered)


def test_confirmation_is_bound_to_reviewed_hash():
    forecast = build_infrastructure_forecast(
        "Customer support agent", _parameters(), transport=FakeAzureMcp()
    )
    with pytest.raises(ValueError, match="does not match"):
        confirm_infrastructure_forecast(forecast, expected_hash="0" * 64)

    unsigned = dict(forecast)
    unsigned.pop("content_hash")
    assert content_hash(unsigned) == forecast["content_hash"]
