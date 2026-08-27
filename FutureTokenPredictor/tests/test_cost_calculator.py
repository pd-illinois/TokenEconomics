"""Tests for the cost calculator module."""

from __future__ import annotations

import pytest

from future_token_predictor.cost_calculator import (
    _get_prices,
    calculate_cost,
    calculate_cost_with_ci,
)
from future_token_predictor.models.schemas import (
    CostEstimate,
    ModalityBreakdown,
    Provider,
)


# ── Price Resolution ─────────────────────────────────────────────────────


class TestGetPrices:
    def test_openai_prices(self):
        prices = _get_prices("gpt-4.1", Provider.OPENAI)
        assert prices["input"] == 2.0
        assert prices["output"] == 8.0

    def test_anthropic_prices(self):
        prices = _get_prices("claude-sonnet-4", Provider.ANTHROPIC)
        assert prices["input"] == 3.0
        assert prices["output"] == 15.0

    def test_google_prices(self):
        prices = _get_prices("gemini-2.5-pro", Provider.GOOGLE)
        assert prices["input"] > 0
        assert prices["output"] > 0

    def test_local_zero_prices(self):
        prices = _get_prices("llama-3.1-8b", Provider.LOCAL)
        assert prices["input"] == 0.0
        assert prices["output"] == 0.0

    def test_auto_resolve_from_model(self):
        prices = _get_prices("claude-opus-4")
        assert prices["input"] == 15.0

    def test_cached_input_present(self):
        prices = _get_prices("claude-sonnet-4", Provider.ANTHROPIC)
        assert "cached_input" in prices
        assert prices["cached_input"] < prices["input"]


# ── Cost Calculation ─────────────────────────────────────────────────────


class TestCalculateCost:
    def test_text_only_gpt41(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        cost = calculate_cost(tokens, "gpt-4.1", provider=Provider.OPENAI)
        # 1000 * 2.0/1M + 500 * 8.0/1M = 0.002 + 0.004 = 0.006
        assert abs(cost - 0.006) < 1e-9

    def test_text_only_claude(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        cost = calculate_cost(tokens, "claude-sonnet-4", provider=Provider.ANTHROPIC)
        # 1000 * 3.0/1M + 500 * 15.0/1M = 0.003 + 0.0075 = 0.0105
        assert abs(cost - 0.0105) < 1e-9

    def test_local_model_free(self):
        tokens = ModalityBreakdown(text_input=10000, text_output=5000)
        cost = calculate_cost(tokens, "llama-3.1-8b", provider=Provider.LOCAL)
        assert cost == 0.0

    def test_cached_tokens_cheaper(self):
        tokens_no_cache = ModalityBreakdown(text_input=1000, text_output=500)
        tokens_with_cache = ModalityBreakdown(
            text_input=500, cached_input=500, text_output=500,
        )
        cost_no_cache = calculate_cost(tokens_no_cache, "gpt-4.1", provider=Provider.OPENAI)
        cost_with_cache = calculate_cost(tokens_with_cache, "gpt-4.1", provider=Provider.OPENAI)
        assert cost_with_cache < cost_no_cache

    def test_multimodal_cost(self):
        tokens = ModalityBreakdown(
            text_input=500, text_output=200,
            image_input=765, document_input=2560,
        )
        cost = calculate_cost(tokens, "gpt-4.1", provider=Provider.OPENAI)
        assert cost > 0

    def test_reasoning_tokens_add_cost(self):
        tokens_no_reasoning = ModalityBreakdown(text_input=1000, text_output=500)
        tokens_with_reasoning = ModalityBreakdown(
            text_input=1000, text_output=500, reasoning=2000,
        )
        cost_base = calculate_cost(tokens_no_reasoning, "o3", provider=Provider.OPENAI)
        cost_reasoning = calculate_cost(tokens_with_reasoning, "o3", provider=Provider.OPENAI)
        assert cost_reasoning > cost_base


# ── Cost with CI ─────────────────────────────────────────────────────────


class TestCalculateCostWithCI:
    def test_returns_cost_estimate(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        result = calculate_cost_with_ci(
            tokens, "gpt-4.1", provider=Provider.OPENAI,
            p5_total=1200, p95_total=1800, p99_total=2000,
        )
        assert isinstance(result, CostEstimate)
        assert result.mean > 0
        assert result.ci_95_low <= result.mean
        assert result.ci_95_high >= result.mean
        assert result.worst_case >= result.ci_95_high

    def test_no_percentiles(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        result = calculate_cost_with_ci(
            tokens, "gpt-4.1", provider=Provider.OPENAI,
        )
        assert result.mean > 0
        assert result.ci_95_low > 0
        assert result.ci_95_high > 0

    def test_ci_ordering(self):
        tokens = ModalityBreakdown(text_input=5000, text_output=2000)
        result = calculate_cost_with_ci(
            tokens, "claude-sonnet-4", provider=Provider.ANTHROPIC,
            p5_total=5000, p95_total=10000, p99_total=15000,
        )
        assert result.ci_95_low <= result.mean <= result.ci_95_high <= result.worst_case


# ── Cross-Provider Comparison ────────────────────────────────────────────


class TestCrossProviderCost:
    def test_same_tokens_different_costs(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        cost_openai = calculate_cost(tokens, "gpt-4.1", provider=Provider.OPENAI)
        cost_anthropic = calculate_cost(tokens, "claude-sonnet-4", provider=Provider.ANTHROPIC)
        cost_local = calculate_cost(tokens, "llama-3.1-8b", provider=Provider.LOCAL)

        # Local should be cheapest (free)
        assert cost_local < cost_openai
        assert cost_local < cost_anthropic
        # All should be non-negative
        assert cost_openai >= 0
        assert cost_anthropic >= 0
        assert cost_local >= 0

    def test_opus_more_expensive_than_sonnet(self):
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        cost_opus = calculate_cost(tokens, "claude-opus-4", provider=Provider.ANTHROPIC)
        cost_sonnet = calculate_cost(tokens, "claude-sonnet-4", provider=Provider.ANTHROPIC)
        assert cost_opus > cost_sonnet

    def test_manual_cost_verification(self):
        """Manually verify cost calculation for GPT-4.1.

        1000 input × $2/1M = $0.002
        500 output × $8/1M = $0.004
        Total = $0.006
        """
        tokens = ModalityBreakdown(text_input=1000, text_output=500)
        cost = calculate_cost(tokens, "gpt-4.1", provider=Provider.OPENAI)
        expected = 1000 * 2.0 / 1_000_000 + 500 * 8.0 / 1_000_000
        assert abs(cost - expected) < 1e-10
