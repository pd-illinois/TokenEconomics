"""Tests for the MCP server tools."""

from __future__ import annotations

import json
import math

import pytest

from future_token_predictor.history import HistoryDatabase, PredictionRecord
from future_token_predictor.mcp_server import call_tool, list_tools
from future_token_predictor.models.schemas import AgentPattern, AgentType, UseCaseProfile


# ── Tool Listing ─────────────────────────────────────────────────────────


class TestToolListing:
    @pytest.mark.asyncio
    async def test_list_tools_returns_eight(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert names == {
            "analyze_workload",
            "predict_token_usage",
            "get_model_pricing",
            "estimate_image_tokens",
            "estimate_document_tokens",
            "compare_providers",
            "refresh_models",
            "record_actual_usage",
        }

    @pytest.mark.asyncio
    async def test_analyze_workload_returns_versioned_evidence(self):
        result = await call_tool("analyze_workload", {
            "description": (
                "Research agents gather evidence and reviewer agents validate results."
            ),
        })
        analysis = json.loads(result[0].text)

        assert analysis["schema_version"] == "1.0"
        assert analysis["rule_set_version"]
        assert analysis["topology"]["selected"] == "multi_agent"
        assert analysis["topology"]["evidence"]

    @pytest.mark.asyncio
    async def test_all_tools_have_schemas(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.inputSchema is not None
            assert tool.inputSchema["type"] == "object"


# ── predict_token_usage ──────────────────────────────────────────────────


class TestPredictTokenUsage:
    @pytest.mark.asyncio
    async def test_description_only(self):
        result = await call_tool("predict_token_usage", {
            "description": "Simple chatbot with GPT-4.1",
        })
        assert len(result) == 1
        text = result[0].text
        assert "Token" in text
        assert "Cost" in text

    @pytest.mark.asyncio
    async def test_structured_params(self):
        result = await call_tool("predict_token_usage", {
            "model": "claude-sonnet-4",
            "provider": "anthropic",
            "agent_pattern": "single_call",
        })
        text = result[0].text
        assert "claude-sonnet-4" in text
        assert "anthropic" in text

    @pytest.mark.asyncio
    async def test_model_auto_resolves_provider(self):
        result = await call_tool("predict_token_usage", {
            "model": "claude-sonnet-4",
        })
        text = result[0].text
        assert "anthropic" in text.lower()

    @pytest.mark.asyncio
    async def test_model_and_scale_overrides_preserve_description_classification(
        self, monkeypatch
    ):
        classified = UseCaseProfile(
            agent_pattern=AgentPattern.TOOL_AGENT,
            agent_type=AgentType.HOSTED,
        )
        monkeypatch.setattr(
            "future_token_predictor.mcp_server.classify",
            lambda description: classified,
        )

        result = await call_tool("predict_token_usage", {
            "description": (
                "An autonomous operations agent continuously monitors cloud "
                "services, executes corrective actions, and validates health."
            ),
            "model": "claude-sonnet-4",
            "provider": "anthropic",
            "users": 1000,
            "calls_per_user_per_day": 10,
            "output_format": "json",
        })
        prediction = json.loads(result[0].text)

        assert prediction["model"] == "claude-sonnet-4"
        assert prediction["provider"] == "anthropic"
        assert prediction["archetype"] == "ToolAgent"
        assert prediction["calculation_trace"]["scale"]["daily_calls"] == 10_000

    @pytest.mark.asyncio
    async def test_multi_agent_prediction_prices_each_agent_model(self):
        result = await call_tool("predict_token_usage", {
            "description": "Two specialist agents collaborate on research and review.",
            "model": "gpt-4.1",
            "provider": "openai",
            "agent_pattern": "multi_agent",
            "agent_models": [
                {
                    "agent_id": "researcher",
                    "role": "Research agent",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                },
                {
                    "agent_id": "reviewer",
                    "role": "Review agent",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4",
                },
            ],
            "users": 100,
            "calls_per_user_per_day": 2,
            "output_format": "json",
        })
        prediction = json.loads(result[0].text)

        assignments = prediction["agent_model_assignments"]
        assert prediction["archetype"] == "MultiAgent"
        assert [item["agent_id"] for item in assignments] == ["researcher", "reviewer"]
        assert [item["model"] for item in assignments] == [
            "gpt-4.1-mini",
            "claude-sonnet-4",
        ]
        assert all(item["pricing_verified"] for item in assignments)
        assert sum(item["allocation_share"] for item in assignments) == pytest.approx(1.0)
        assert prediction["cost_per_call"]["mean"] == pytest.approx(
            sum(item["cost_per_invocation_usd"] for item in assignments),
            abs=1e-6,
        )
        assert prediction["calculation_trace"]["agent_models"]["allocation_method"] == (
            "normalized modeled turn weights"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("turn_weight", [0, -1, math.inf, math.nan])
    async def test_agent_models_reject_non_positive_or_non_finite_weights(
        self, turn_weight
    ):
        with pytest.raises(ValueError, match="finite and greater than zero"):
            await call_tool("predict_token_usage", {
                "agent_models": [
                    {
                        "agent_id": "researcher",
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "turn_weight": turn_weight,
                    },
                    {
                        "agent_id": "reviewer",
                        "provider": "anthropic",
                        "model": "claude-sonnet-4",
                    },
                ],
            })

    @pytest.mark.asyncio
    async def test_agent_models_require_at_least_two_assignments(self):
        with pytest.raises(ValueError, match="at least two"):
            await call_tool("predict_token_usage", {
                "agent_models": [{
                    "agent_id": "researcher",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                }],
            })

    @pytest.mark.asyncio
    async def test_agent_models_reject_blank_model_after_normalization(self):
        with pytest.raises(ValueError, match="non-empty provider and model"):
            await call_tool("predict_token_usage", {
                "agent_models": [
                    {
                        "agent_id": "researcher",
                        "provider": "openai",
                        "model": "   ",
                    },
                    {
                        "agent_id": "reviewer",
                        "provider": "anthropic",
                        "model": "claude-sonnet-4",
                    },
                ],
            })

    @pytest.mark.asyncio
    async def test_custom_function_is_reported_as_externally_billed_unpriced(self):
        result = await call_tool("predict_token_usage", {
            "description": (
                "A support agent executes approved refunds and shipment updates "
                "through enterprise systems."
            ),
            "output_format": "json",
        })
        prediction = json.loads(result[0].text)

        exclusions = prediction["tool_costs_per_call"]["unpriced_external_tools"]
        assert exclusions == [{
            "tool": "custom_function",
            "pricing_status": "externally_billed_unpriced",
        }]
        assert prediction["calculation_trace"]["tool_costs"]["is_complete"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("agent_pattern", "expected_archetype"),
        [
            ("rag_pipeline", "RAG_Pipeline"),
            ("tool_agent", "ToolAgent"),
            ("react_agent", "ToolAgent"),
            ("workflow", "Workflow"),
            ("multi_agent", "MultiAgent"),
        ],
    )
    async def test_structured_agent_pattern_controls_workflow_prediction(
        self, agent_pattern, expected_archetype
    ):
        arguments = {
            "description": f"{agent_pattern} RAG using Azure AI Search",
            "model": "gpt-4.1",
            "provider": "azure_openai",
            "agent_pattern": agent_pattern,
            "modalities": ["text", "document"],
            "tools": ["file_search"],
            "users": 1000,
            "calls_per_user_per_day": 10,
            "output_format": "json",
        }
        if agent_pattern == "multi_agent":
            arguments["multi_agent_count"] = 4

        result = await call_tool("predict_token_usage", arguments)
        prediction = json.loads(result[0].text)

        assert prediction["archetype"] == expected_archetype
        token_components = prediction["tokens_per_call"]
        assert token_components["total"] == pytest.approx(
            sum(value for key, value in token_components.items() if key != "total"),
            abs=2,
        )
        cost_trace = prediction["calculation_trace"]["cost_per_call"]
        assert cost_trace["result_usd"] == pytest.approx(
            sum(component["cost_usd"] for component in cost_trace["components"])
        )
        assert prediction["cost_per_call"]["mean"] == pytest.approx(
            cost_trace["result_usd"], abs=1e-6
        )
        scale_trace = prediction["calculation_trace"]["scale"]
        assert scale_trace["daily_calls"] == 10_000
        assert prediction["daily_cost"]["mean"] == pytest.approx(
            scale_trace["daily_result_usd"], abs=1e-6
        )
        assert prediction["monthly_cost"]["mean"] == pytest.approx(
            prediction["daily_cost"]["mean"] * 30, abs=3e-5
        )
        assert prediction["annual_cost"]["mean"] == pytest.approx(
            prediction["daily_cost"]["mean"] * 365, abs=4e-4
        )
        tool_trace = prediction["calculation_trace"]["tool_costs"]
        assert tool_trace["included_in_model_cost"] is False
        assert prediction["tool_costs_per_call"]["total_usd"] == pytest.approx(
            sum(component["cost_usd"] for component in tool_trace["components_per_invocation"])
        )
        assert prediction["tokens_p5"] <= prediction["tokens_p50"] <= prediction["tokens_p95"]
        if agent_pattern == "rag_pipeline":
            assert prediction["bound_provenance"]["method"] == "heuristic_multiplier"
        else:
            assert prediction["bound_provenance"]["method"] == "monte_carlo_quantile"
            assert (
                prediction["tokens_p5"]
                <= prediction["tokens_per_call"]["total"]
                <= prediction["tokens_p95"]
            )
            assert (
                prediction["cost_per_call"]["range_low"]
                <= prediction["cost_per_call"]["mean"]
                <= prediction["cost_per_call"]["range_high"]
            )


class TestRecordActualUsage:
    @pytest.mark.asyncio
    async def test_records_and_rejects_duplicate(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        db = HistoryDatabase(db_path)
        prediction_id = db.record_prediction(PredictionRecord(
            model="gpt-4.1",
            provider="openai",
            archetype="SingleCall_TextOnly",
            predicted_total=1000.0,
        ))
        db.close()

        arguments = {
            "prediction_id": prediction_id,
            "actual_text_input": 600,
            "actual_text_output": 500,
            "actual_cost": 0.01,
            "db_path": db_path,
        }
        first = json.loads((await call_tool("record_actual_usage", arguments))[0].text)
        second = json.loads((await call_tool("record_actual_usage", arguments))[0].text)

        assert first == {"prediction_id": prediction_id, "status": "updated"}
        assert second == {
            "prediction_id": prediction_id,
            "status": "already_recorded",
        }

    @pytest.mark.asyncio
    async def test_reports_unknown_prediction(self, tmp_path):
        result = await call_tool("record_actual_usage", {
            "prediction_id": 99999,
            "actual_total": 100,
            "db_path": str(tmp_path / "history.db"),
        })

        assert json.loads(result[0].text)["status"] == "not_found"


# ── get_model_pricing ────────────────────────────────────────────────────


class TestGetModelPricing:
    @pytest.mark.asyncio
    async def test_openai_pricing(self):
        result = await call_tool("get_model_pricing", {
            "model": "gpt-4.1",
            "provider": "openai",
        })
        text = result[0].text
        assert "gpt-4.1" in text
        assert "$" in text

    @pytest.mark.asyncio
    async def test_anthropic_pricing(self):
        result = await call_tool("get_model_pricing", {
            "model": "claude-sonnet-4",
            "provider": "anthropic",
        })
        text = result[0].text
        assert "3.0000" in text  # $3/1M input
        assert "15.0000" in text  # $15/1M output

    @pytest.mark.asyncio
    async def test_auto_detect_provider(self):
        result = await call_tool("get_model_pricing", {
            "model": "claude-opus-4",
        })
        text = result[0].text
        assert "15.0000" in text  # $15/1M input


# ── estimate_image_tokens ────────────────────────────────────────────────


class TestEstimateImageTokens:
    @pytest.mark.asyncio
    async def test_openai_image(self):
        result = await call_tool("estimate_image_tokens", {
            "width": 1024,
            "height": 1024,
            "provider": "openai",
        })
        text = result[0].text
        assert "765" in text

    @pytest.mark.asyncio
    async def test_google_image(self):
        result = await call_tool("estimate_image_tokens", {
            "width": 1024,
            "height": 1024,
            "provider": "google",
        })
        text = result[0].text
        assert "258" in text

    @pytest.mark.asyncio
    async def test_multiple_images(self):
        result = await call_tool("estimate_image_tokens", {
            "width": 1024,
            "height": 1024,
            "count": 3,
            "provider": "openai",
        })
        text = result[0].text
        assert "2,295" in text  # 765 × 3


# ── estimate_document_tokens ─────────────────────────────────────────────


class TestEstimateDocumentTokens:
    @pytest.mark.asyncio
    async def test_file_search(self):
        result = await call_tool("estimate_document_tokens", {
            "pages": 10,
            "strategy": "file_search",
            "top_k": 5,
        })
        text = result[0].text
        assert "2,560" in text

    @pytest.mark.asyncio
    async def test_direct_strategy(self):
        result = await call_tool("estimate_document_tokens", {
            "pages": 10,
            "strategy": "direct",
        })
        text = result[0].text
        assert "6,500" in text


# ── compare_providers ────────────────────────────────────────────────────


class TestCompareProviders:
    @pytest.mark.asyncio
    async def test_compare_all(self):
        result = await call_tool("compare_providers", {
            "input_tokens": 1000,
            "output_tokens": 500,
            "calls": 1000,
        })
        text = result[0].text
        assert "Provider" in text
        assert "Cost" in text
        assert "Cheapest" in text

    @pytest.mark.asyncio
    async def test_compare_subset(self):
        result = await call_tool("compare_providers", {
            "providers": ["openai", "anthropic"],
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        text = result[0].text
        assert "OpenAI" in text
        assert "Anthropic" in text

    @pytest.mark.asyncio
    async def test_local_cheapest(self):
        result = await call_tool("compare_providers", {
            "providers": ["openai", "local"],
            "input_tokens": 1000,
            "output_tokens": 500,
        })
        text = result[0].text
        # Local should appear first (sorted by cost)
        lines = text.split("\n")
        data_lines = [l for l in lines if l.startswith("| ") and "Provider" not in l and "---" not in l]
        assert len(data_lines) >= 2


# ── Unknown Tool ─────────────────────────────────────────────────────────


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await call_tool("nonexistent_tool", {})
        assert "Unknown tool" in result[0].text


# ── Error Handling ───────────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_invalid_provider_enum(self):
        with pytest.raises(ValueError):
            await call_tool("get_model_pricing", {
                "model": "gpt-4.1",
                "provider": "not_a_real_provider",
            })

    @pytest.mark.asyncio
    async def test_predict_empty_args(self):
        """predict with no description and no structured params should raise."""
        with pytest.raises(ValueError, match="Provide either"):
            await call_tool("predict_token_usage", {})

    @pytest.mark.asyncio
    async def test_image_tokens_missing_provider(self):
        """Image tokens without provider should default gracefully."""
        result = await call_tool("estimate_image_tokens", {
            "width": 512, "height": 512,
        })
        assert "Total tokens" in result[0].text
