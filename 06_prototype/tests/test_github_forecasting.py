from __future__ import annotations

import json
from datetime import date

import pytest

from costgov.commercial_forecasting import CommercialForecastError
from costgov.github_forecasting import (
    DEFAULT_RATE_CARD,
    GitHubTokenUsage,
    forecast_github_copilot,
    load_github_rate_card,
)


def _card():
    return load_github_rate_card(as_of=date(2026, 8, 20))


def test_github_ai_credits_are_derived_from_token_prices_and_allowance():
    forecast = forecast_github_copilot(
        _card(),
        model_id="gpt-5.4",
        plan_id="copilot_business",
        seat_count=1,
        usage=GitHubTokenUsage(
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            cache_write_tokens=0,
            output_tokens=100_000,
            max_input_tokens_per_request=100_000,
        ),
        fixed_seat_cost_usd=19,
        additional_usage_enabled=True,
    )

    assert forecast["model_usage_cost_usd"] == pytest.approx(4.25)
    assert forecast["gross_github_ai_credits"] == pytest.approx(425)
    assert forecast["included_github_ai_credits"] == pytest.approx(425)
    assert forecast["additional_github_ai_credits"] == 0
    assert forecast["additional_usage_cost_usd"] == 0
    assert forecast["modeled_total_cost_usd"] == pytest.approx(19)
    assert forecast["credit_definition"]["not_microsoft_copilot_credits"] is True


def test_disabled_overage_surfaces_capacity_risk_without_success_shaped_cost():
    forecast = forecast_github_copilot(
        _card(),
        model_id="gpt-5.4",
        plan_id="copilot_business",
        seat_count=1,
        usage=GitHubTokenUsage(
            input_tokens=10_000_000,
            cached_input_tokens=0,
            cache_write_tokens=0,
            output_tokens=1_000_000,
            max_input_tokens_per_request=100_000,
        ),
        fixed_seat_cost_usd=19,
        additional_usage_enabled=False,
    )

    assert forecast["status"] == "capacity_risk"
    assert forecast["additional_github_ai_credits"] > 0
    assert forecast["additional_usage_cost_usd"] is None
    assert forecast["modeled_total_cost_usd"] is None


def test_stale_github_rate_card_fails_closed():
    with pytest.raises(CommercialForecastError, match="stale"):
        load_github_rate_card(as_of=date(2026, 9, 2))


def test_invalid_github_rate_evidence_fails_closed(tmp_path):
    document = json.loads(DEFAULT_RATE_CARD.read_text(encoding="utf-8"))
    document["models"]["gpt-5.4"]["output_usd_per_million"] = -1
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CommercialForecastError, match="finite and non-negative"):
        load_github_rate_card(path, as_of=date(2026, 8, 20))


def test_long_context_rates_are_selected_from_per_request_evidence():
    forecast = forecast_github_copilot(
        _card(),
        model_id="gpt-5.4",
        plan_id="copilot_business",
        seat_count=1,
        usage=GitHubTokenUsage(
            input_tokens=1_000_000,
            cached_input_tokens=0,
            cache_write_tokens=0,
            output_tokens=100_000,
            max_input_tokens_per_request=300_000,
        ),
        fixed_seat_cost_usd=19,
        additional_usage_enabled=True,
    )

    assert forecast["pricing_tier"] == "long_context"
    assert forecast["model_usage_cost_usd"] == pytest.approx(7.25)
    assert forecast["gross_github_ai_credits"] == pytest.approx(725)


def test_tiered_model_requires_per_request_context_evidence():
    with pytest.raises(CommercialForecastError, match="max_input_tokens_per_request"):
        forecast_github_copilot(
            _card(),
            model_id="gpt-5.4",
            plan_id="copilot_business",
            seat_count=1,
            usage=GitHubTokenUsage(1_000, 0, 0, 100),
            fixed_seat_cost_usd=19,
            additional_usage_enabled=True,
        )


def test_disabled_overage_keeps_known_fixed_cost_when_usage_is_within_allowance():
    forecast = forecast_github_copilot(
        _card(),
        model_id="gpt-5.4",
        plan_id="copilot_business",
        seat_count=1,
        usage=GitHubTokenUsage(1_000, 0, 0, 100, 100_000),
        fixed_seat_cost_usd=19,
        additional_usage_enabled=False,
    )

    assert forecast["status"] == "complete"
    assert forecast["additional_usage_cost_usd"] == 0
    assert forecast["modeled_total_cost_usd"] == 19


def test_missing_source_timestamp_fails_during_rate_loading(tmp_path):
    document = json.loads(DEFAULT_RATE_CARD.read_text(encoding="utf-8"))
    document.pop("source_retrieved_at")
    path = tmp_path / "missing-provenance.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CommercialForecastError, match="incomplete"):
        load_github_rate_card(path, as_of=date(2026, 8, 20))


def test_github_usage_requires_a_positive_finite_quantity():
    with pytest.raises(CommercialForecastError, match="at least one"):
        GitHubTokenUsage(0, 0, 0, 0)
