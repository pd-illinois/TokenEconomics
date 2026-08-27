from __future__ import annotations

from datetime import date

import pytest

from costgov.commercial_contracts import (
    BillingDisposition,
    EntitlementContext,
    EvidenceStatus,
    PredictedUsageEvent,
)
from costgov.commercial_entitlements import evaluate_entitlement
from costgov.commercial_forecasting import (
    CommercialForecastError,
    PurchasePortfolio,
    ScenarioPrior,
    allocate_purchase_portfolio,
    compose_hybrid_forecast,
    forecast_copilot_studio,
    forecast_variable_scenario,
)
from costgov.commercial_rate_cards import load_copilot_studio_rate_card


def _context(**overrides) -> EntitlementContext:
    values = {
        "user_segment": "employees",
        "audience_type": "b2e",
        "authenticated": True,
        "identity_mode": "licensed_user",
        "license_sku": "microsoft_365_copilot",
        "channel": "microsoft_365_copilot",
        "trigger_type": "interactive",
        "product_boundary": "copilot_studio",
        "evidence_version": "entitlement-input.v1",
        "fair_use_assumption": "within_current_documented_limits",
    }
    values.update(overrides)
    return EntitlementContext(**values)


def _card():
    return load_copilot_studio_rate_card(as_of=date(2026, 8, 20))


def _event(meter_id: str, quantity: float, *, event_id: str | None = None):
    meter = _card().meter(meter_id)
    return PredictedUsageEvent(
        event_id=event_id or f"event-{meter_id}",
        feature=meter.feature,
        native_unit=meter.native_unit,
        quantity=quantity,
        meter_id=meter_id,
        disposition=BillingDisposition.UNKNOWN_REQUIRES_POLICY,
        evidence_status=EvidenceStatus.MODELED,
        quantity_source="user_confirmed.v1",
    )


def test_entitlement_requires_identity_and_does_not_infer_from_b2e():
    meter = _card().meter("generative_answer")

    decision = evaluate_entitlement(
        meter, _context(authenticated=None, license_sku=None)
    )

    assert decision.disposition is BillingDisposition.UNKNOWN_REQUIRES_POLICY
    assert decision.reason_code == "material_identity_evidence_missing"


def test_entitlement_is_meter_specific_for_test_flows_and_ai_tools():
    context = _context(test_mode=True, trigger_type="flow_designer_test")

    flow = evaluate_entitlement(_card().meter("agent_flow_actions"), context)
    tool = evaluate_entitlement(_card().meter("premium_ai_tool"), context)

    assert flow.disposition is BillingDisposition.TEST_EXEMPT
    assert tool.disposition is BillingDisposition.BILLABLE


def test_computer_use_and_nonqualifying_flow_triggers_remain_billable():
    computer = evaluate_entitlement(
        _card().meter("agent_action"), _context(computer_use=True)
    )
    scheduled_flow = evaluate_entitlement(
        _card().meter("agent_flow_actions"),
        _context(trigger_type="scheduled"),
    )

    assert computer.disposition is BillingDisposition.BILLABLE
    assert scheduled_flow.disposition is BillingDisposition.BILLABLE


def test_documented_feature_rates_compose_additively_without_token_conversion():
    forecast = forecast_copilot_studio(
        _card(),
        [
            _event("generative_answer", 2),
            _event("tenant_graph_grounding", 1),
            _event("agent_action", 2),
            _event("premium_ai_tool", 2000),
        ],
        _context(
            audience_type="b2c",
            authenticated=False,
            identity_mode="anonymous",
            license_sku=None,
            channel="website",
            fair_use_assumption=None,
        ),
    )

    assert forecast["status"] == "complete"
    assert forecast["total_copilot_credits"] == pytest.approx(44)
    assert forecast["native_quantities"]["token_or_character"] == 2000
    assert "model_tokens" not in forecast


def test_material_unknown_entitlement_blocks_decision_grade_forecast():
    forecast = forecast_copilot_studio(
        _card(),
        [_event("generative_answer", 1)],
        _context(authenticated=None, license_sku=None),
    )

    assert forecast["status"] == "needs_clarification"
    assert forecast["total_copilot_credits"] is None
    assert forecast["blocking_unknowns"][0]["meter_id"] == "generative_answer"


def test_byom_hybrid_retains_independent_credit_and_model_components():
    commercial = forecast_copilot_studio(
        _card(),
        [_event("agent_action", 3), _event("premium_ai_tool", 4000)],
        _context(
            audience_type="b2c",
            authenticated=False,
            identity_mode="anonymous",
            license_sku=None,
            channel="website",
            product_boundary="copilot_studio_byom",
            fair_use_assumption=None,
        ),
    )
    token_subforecast = {
        "prediction_id": 91,
        "scope": "model_invocation",
        "provider": "azure_openai",
        "model": "gpt-4.1",
        "pricing_version": "catalog-2026-08-20",
        "cost_usd": 2.5,
        "exclusions": ["infrastructure"],
    }

    hybrid = compose_hybrid_forecast(commercial, token_subforecast)

    assert commercial["total_copilot_credits"] == 15
    assert hybrid["commercial"]["total_copilot_credits"] == 15
    assert hybrid["token_subforecast"]["prediction_id"] == 91
    assert hybrid["total_usd"] is None
    assert "infrastructure" in hybrid["unpriced_exclusions"]
    assert "commercial_purchase_economics_unavailable" in (
        hybrid["unpriced_exclusions"]
    )


def test_purchase_allocation_keeps_cash_commitment_and_allocation_views_separate():
    portfolio = PurchasePortfolio(
        version="portfolio.v1",
        product="Microsoft Copilot Studio",
        environment="prod",
        committed_credits=1000,
        committed_cost_usd=8,
        payg_enabled=True,
        payg_rate_usd_per_credit=0.01,
        fixed_seat_cost_usd=300,
        scope_evidence_version="allocation.v1",
    )

    economics = allocate_purchase_portfolio(
        1200,
        portfolio,
        product="Microsoft Copilot Studio",
        environment="prod",
    )

    assert economics["retail_cost_usd"] == 12
    assert economics["commitment_drawdown_credits"] == 1000
    assert economics["unused_commitment_credits"] == 0
    assert economics["incremental_cash_cost_usd"] == 2
    assert economics["fixed_allocation_cost_usd"] == 300
    assert economics["amortized_cost_usd"] == 310


def test_purchase_allocation_fails_closed_on_scope_or_overage():
    portfolio = PurchasePortfolio(
        version="portfolio.v1",
        product="Microsoft Copilot Studio",
        environment="prod",
        committed_credits=100,
        committed_cost_usd=1,
        payg_enabled=False,
        payg_rate_usd_per_credit=0.01,
        fixed_seat_cost_usd=0,
        scope_evidence_version="allocation.v1",
    )

    with pytest.raises(CommercialForecastError, match="scope"):
        allocate_purchase_portfolio(
            50, portfolio, product="Microsoft Copilot Studio", environment="dev"
        )

    economics = allocate_purchase_portfolio(
        150,
        portfolio,
        product="Microsoft Copilot Studio",
        environment="prod",
    )
    assert economics["status"] == "capacity_risk"
    assert economics["unfunded_credits"] == 50
    assert economics["incremental_cash_cost_usd"] is None


def test_purchase_allocation_without_overage_prices_fully_funded_usage():
    portfolio = PurchasePortfolio(
        version="portfolio.v1",
        product="Microsoft Copilot",
        environment="prod",
        committed_credits=100,
        committed_cost_usd=1,
        payg_enabled=False,
        payg_rate_usd_per_credit=0.01,
        fixed_seat_cost_usd=50,
        scope_evidence_version="allocation.v1",
    )

    economics = allocate_purchase_portfolio(
        0,
        portfolio,
        product="Microsoft Copilot",
        environment="prod",
    )

    assert economics["status"] == "complete"
    assert economics["incremental_cash_cost_usd"] == 0
    assert economics["amortized_cost_usd"] == 51


def test_variable_scenario_is_seeded_and_labeled_modeled():
    prior = ScenarioPrior(
        prior_id="cowork-knowledge-heavy",
        version="1.0",
        product="Copilot Cowork",
        segment="knowledge_workers",
        credits_per_task_p50=25,
        credits_per_task_p95=90,
        source_url="https://learn.microsoft.com/copilot",
        source_retrieved_at="2026-08-20T16:00:00Z",
        evidence_status=EvidenceStatus.MODELED,
    )

    first = forecast_variable_scenario(prior, task_count=100, seed=17)
    second = forecast_variable_scenario(prior, task_count=100, seed=17)

    assert first == second
    assert first["evidence_status"] == "modeled"
    assert first["calibration_status"] == "uncalibrated"
    assert first["p95_copilot_credits"] > first["p50_copilot_credits"]
