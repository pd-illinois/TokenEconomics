"""Tests for the predictor orchestrator and report formatter."""

from __future__ import annotations

import pytest

from future_token_predictor.classifier import classify
from future_token_predictor.models.schemas import (
    AgentPattern,
    AgentType,
    Modality,
    Provider,
    Tool,
    UseCaseProfile,
)
from future_token_predictor.predictor import predict
from future_token_predictor.report import format_report
from future_token_predictor.report import build_json_output


# ── Predictor ────────────────────────────────────────────────────────────


class TestPredictor:
    def test_classifier_accepts_comma_formatted_scale(self):
        profile = classify(
            "RAG solution for 1,000 users, 2,500 queries per user per day"
        )

        assert profile.users == 1000
        assert profile.calls_per_user_per_day == 2500

    def test_predict_from_description(self):
        result = predict(description="Simple chatbot with GPT-4.1, 10 users")
        assert result.model == "gpt-4.1"
        assert result.provider == "openai"
        assert result.tokens_per_call.total > 0
        assert result.cost_per_call.mean > 0

    def test_predict_from_profile(self):
        profile = UseCaseProfile()
        profile.model = "claude-sonnet-4"
        profile.provider = Provider.ANTHROPIC
        result = predict(profile=profile)
        assert result.model == "claude-sonnet-4"
        assert result.provider == "anthropic"
        assert result.cost_per_call.mean > 0

    def test_predict_requires_input(self):
        with pytest.raises(ValueError, match="Provide either"):
            predict()

    def test_predict_anthropic_description(self):
        result = predict(description="ReAct agent using Claude Sonnet 4 with RAG")
        assert result.provider == "anthropic"
        assert result.model == "claude-sonnet-4"

    def test_predict_google_description(self):
        result = predict(description="Use Gemini 2.5 Pro for document analysis")
        assert result.provider == "google"
        assert result.model == "gemini-2.5-pro"

    def test_predict_local_free(self):
        result = predict(description="Run Llama 3.1 8B locally for testing")
        assert result.provider == "local"
        assert result.cost_per_call.mean == 0.0
        assert result.monthly_cost_usd.mean == 0.0

    def test_pricing_verified(self):
        result = predict(description="Simple GPT-4.1 chatbot")
        assert result.pricing_verified is True

    def test_scaled_projections_positive(self):
        result = predict(description="GPT-4.1 chatbot, 100 users, 5 queries per day")
        assert result.daily_tokens > 0
        assert result.daily_cost_usd.mean > 0
        assert result.monthly_cost_usd.mean > result.daily_cost_usd.mean
        assert result.annual_cost_usd.mean > result.monthly_cost_usd.mean

    def test_single_call_bound_provenance(self):
        result = predict(description="Simple GPT-4.1 chatbot")
        output = build_json_output(result)

        assert result.tokens_p50 > 0
        assert output["bound_provenance"] == {
            "method": "heuristic_multiplier",
            "samples": 1,
            "seed": None,
        }
        assert "range_high" in output["cost_per_call"]
        assert "ci_95_high" not in output["cost_per_call"]

    def test_workflow_bound_provenance(self):
        profile = UseCaseProfile(
            agent_type=AgentType.HOSTED,
            agent_pattern=AgentPattern.REACT_AGENT,
        )
        result = predict(profile=profile)

        assert result.bound_method == "monte_carlo_quantile"
        assert result.bound_samples == 1000
        assert result.bound_seed == 42
        assert result.tokens_p5 <= result.tokens_p50 <= result.tokens_p95

    def test_rag_calculation_trace_reconciles_cost_and_scale(self):
        profile = UseCaseProfile(
            agent_pattern=AgentPattern.RAG_PIPELINE,
            modalities=[Modality.TEXT, Modality.DOCUMENT],
            tools=[Tool.FILE_SEARCH],
            users=1000,
            calls_per_user_per_day=10,
        )
        result = predict(profile=profile, enable_tier2=False, enable_tier3=False)
        output = build_json_output(result)
        trace = output["calculation_trace"]

        assert trace["tokens"]["tier1_baseline"] == {
            "text_input": 1200.0,
            "text_output": 1200.0,
            "document_input": 2560.0,
            "reasoning": 0.0,
            "total": 4960.0,
        }
        assert trace["tokens"]["tokenizer"] == "o200k_base"
        assert trace["tokens"]["tokenizer_applied"] is False
        component_total = sum(
            component["cost_usd"]
            for component in trace["cost_per_call"]["components"]
        )
        assert component_total == pytest.approx(output["cost_per_call"]["mean"])
        assert trace["scale"]["daily_calls"] == 10_000
        assert trace["scale"]["monthly_result_usd"] == pytest.approx(
            trace["scale"]["daily_result_usd"] * 30
        )
        assert trace["scale"]["annual_result_usd"] == pytest.approx(
            trace["scale"]["daily_result_usd"] * 365
        )
        assert trace["scale"]["cache_assumptions"]["input_rate_per_million"] == 2.0
        assert trace["scale"]["cache_assumptions"]["cached_input_rate_per_million"] == 0.5
        assert trace["tool_costs"]["included_in_model_cost"] is False
        assert trace["tool_costs"]["per_invocation_usd"] == pytest.approx(0.005)
        assert trace["tool_costs"]["storage_usd_per_day"] == pytest.approx(0.011)
        assert trace["tool_costs"]["daily_result_usd"] == pytest.approx(50.011)
        assert trace["tool_costs"]["monthly_result_usd"] == pytest.approx(1500.33)

    @pytest.mark.parametrize(
        ("description", "expected_archetype"),
        [
            (
                "An autonomous ReAct agent iteratively retrieves documents from a knowledge base.",
                "ReAct_Agent",
            ),
            (
                "Research agents and reviewer agents collaborate using retrieval over documents.",
                "MultiAgent",
            ),
            (
                "A bounded workflow retrieves and reviews contract documents.",
                "Workflow",
            ),
        ],
    )
    def test_retrieval_is_a_component_not_a_topology_override(
        self, description, expected_archetype
    ):
        result = predict(
            description=description,
            enable_tier2=False,
            enable_tier3=False,
        )

        assert result.archetype == expected_archetype
        assert result.tokens_per_call.document_input > 0
        assert result.bound_method == "monte_carlo_quantile"
        assert result.tokens_p5 <= result.tokens_p50 <= result.tokens_p95

    def test_explicit_workflow_steps_change_iteration_arithmetic(self):
        short = predict(
            description="A 2-step workflow reviews contract documents.",
            enable_tier2=False,
            enable_tier3=False,
        )
        long = predict(
            description="An 8-step workflow reviews contract documents.",
            enable_tier2=False,
            enable_tier3=False,
        )

        assert short.archetype == long.archetype == "Workflow"
        assert long.tokens_per_call.total > short.tokens_per_call.total * 2
        assert long.tokens_per_call.document_input == short.tokens_per_call.document_input


# ── Report ───────────────────────────────────────────────────────────────


class TestReport:
    def test_format_report_has_sections(self):
        result = predict(description="GPT-4.1 chatbot, 50 users")
        report = format_report(result)
        assert "# Token & Cost Prediction Report" in report
        assert "**Model:**" in report
        assert "**Provider:**" in report
        assert "Per-Call Token Estimate" in report
        assert "Per-Call Cost" in report
        assert "Scaled Projections" in report

    def test_format_report_anthropic(self):
        result = predict(description="Claude Sonnet 4 RAG agent")
        report = format_report(result)
        assert "claude-sonnet-4" in report
        assert "anthropic" in report

    def test_report_contains_cost_values(self):
        result = predict(description="GPT-4.1 agent, 10 users, 5 calls per day")
        report = format_report(result)
        assert "$" in report
        assert "Monthly" in report or "monthly" in report

    def test_optimizations_in_report(self):
        result = predict(description="Complex GPT-4.1 agent with file search and large documents")
        report = format_report(result)
        # Should have at least one optimization suggestion
        if result.optimizations:
            assert "Optimization" in report
