from __future__ import annotations

import math

import pytest

from costgov.mcp_prediction import McpPredictorClient


def test_real_stdio_mcp_analysis_returns_versioned_evidence():
    analysis = McpPredictorClient(".").analyze(
        "Research agents gather evidence and reviewer agents validate results."
    )

    assert analysis["schema_version"] == "1.0"
    assert analysis["rule_set_version"]
    assert len(analysis["description_hash"]) == 64
    assert analysis["topology"]["selected"] == "multi_agent"
    assert analysis["topology"]["evidence"]


def test_real_stdio_mcp_prediction_returns_structured_costs():
    client = McpPredictorClient(".")
    description = (
        "GPT-4.1 RAG solution for 1000 users with Azure AI Search, VNets, "
        "and private endpoints"
    )
    analysis = client.analyze(description)
    result = client.predict(
        description,
        {
            "analysis": analysis,
            "confirmed_profile": {
                "agent_pattern": analysis["topology"]["selected"],
                "multi_agent_count": analysis["agent_count"]["value"],
                "modalities": analysis["modalities"],
                "tools": analysis["tools"],
            },
        },
    )

    prediction = result["prediction"]
    assert result["status"] == "complete"
    assert prediction["prediction_id"] is not None
    assert prediction["archetype"]
    assert prediction["tokens_per_call"]["total"] > 0
    assert prediction["monthly_cost"]["mean"] > 0
    assert result["intake"]["users"] == 1000
    assert result["intake"]["agent_pattern"] == "rag_pipeline"
    assert result["intake"]["analysis"]["rule_set_version"]
    assert prediction["missing_parameters"] == ["complexity (defaulted to medium)"]
    assert prediction["bound_provenance"]["method"]
    assert prediction["calculation_trace"]["cost_per_call"]["components"]
    assert prediction["calculation_trace"]["scale"]["daily_calls"] == 1000
    assert result["infrastructure"]["status"] == "not_estimated"


def test_mcp_prediction_uses_explicit_scale_overrides():
    result = McpPredictorClient(".").predict(
        "RAG solution for 1,000 users",
        {"model": "gpt-4.1", "users": 250, "calls_per_user_per_day": 4},
    )

    assert result["intake"]["users"] == 250
    assert result["intake"]["calls_per_user_per_day"] == 4
    assert result["prediction"]["daily_tokens"] > 0


def test_prediction_arguments_preserve_selected_provider_model_offering():
    arguments, intake = McpPredictorClient._build_arguments(
        "General assistant",
        {"provider": "anthropic", "model": "claude-sonnet-4"},
    )

    assert arguments["provider"] == "anthropic"
    assert arguments["model"] == "claude-sonnet-4"
    assert intake["provider"] == "anthropic"


def test_prediction_arguments_preserve_per_agent_model_assignments():
    agent_models = [
        {
            "agent_id": "researcher",
            "role": "Research agent",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "turn_weight": 2,
        },
        {
            "agent_id": "reviewer",
            "role": "Review agent",
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "turn_weight": 1,
        },
    ]

    arguments, intake = McpPredictorClient._build_arguments(
        "Research and review agents collaborate on an enterprise report.",
        {
            "provider": "openai",
            "model": "gpt-4.1",
            "agent_models": agent_models,
        },
    )

    assert arguments["agent_models"] == agent_models
    assert intake["agent_models"] == agent_models
    assert arguments["agent_pattern"] == "multi_agent"
    assert arguments["multi_agent_count"] == 2


@pytest.mark.parametrize("turn_weight", [0, -1, math.inf, math.nan])
def test_prediction_arguments_reject_invalid_agent_turn_weight(turn_weight):
    with pytest.raises(ValueError, match="finite and greater than zero"):
        McpPredictorClient._build_arguments(
            "Two agents collaborate.",
            {"agent_models": [
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
            ]},
        )


def test_prediction_arguments_require_two_agent_models():
    with pytest.raises(ValueError, match="at least two"):
        McpPredictorClient._build_arguments(
            "One agent.",
            {"agent_models": [{
                "agent_id": "researcher",
                "provider": "openai",
                "model": "gpt-4.1-mini",
            }]},
        )


@pytest.mark.parametrize(
    ("description", "expected_pattern"),
    [
        ("RAG question answering with Azure AI Search", "rag_pipeline"),
        ("Autonomous ReAct agent loop with RAG and Azure AI Search", "tool_agent"),
        ("Deterministic multi-step workflow with RAG and Azure AI Search", "workflow"),
        ("A 4 agent multi-agent research system with RAG and Azure AI Search", "multi_agent"),
    ],
)
def test_prediction_arguments_do_not_classify_prose_in_studio(description, expected_pattern):
    arguments, intake = McpPredictorClient._build_arguments(
        description,
        {"provider": "azure_openai", "model": "gpt-4.1"},
    )

    assert "agent_pattern" not in arguments
    assert "agent_pattern" not in intake
    assert "modalities" not in arguments
    assert "tools" not in arguments
    assert "complexity" not in arguments


def test_confirmed_profile_is_submitted_as_explicit_prediction_evidence():
    confirmed_profile = {
        "agent_pattern": "multi_agent",
        "multi_agent_count": 3,
        "modalities": ["text", "document"],
        "tools": ["file_search", "custom_function"],
    }

    arguments, intake = McpPredictorClient._build_arguments(
        "Research, writer, and reviewer agents collaborate.",
        {
            "provider": "openai",
            "model": "gpt-4.1",
            "analysis": {"schema_version": "1.0", "rule_set_version": "rules-1"},
            "confirmed_profile": confirmed_profile,
        },
    )

    assert arguments["agent_pattern"] == "multi_agent"
    assert arguments["multi_agent_count"] == 3
    assert arguments["modalities"] == ["text", "document"]
    assert arguments["tools"] == ["file_search", "custom_function"]
    assert "analysis" not in arguments
    assert intake["analysis"]["rule_set_version"] == "rules-1"
    assert intake["confirmed_profile"] == confirmed_profile


def test_confirmed_quantity_profile_is_submitted_as_prediction_evidence():
    arguments, intake = McpPredictorClient._build_arguments(
        "A workflow searches documents.",
        {"confirmed_profile": {
            "agent_pattern": "workflow", "multi_agent_count": 1,
            "modalities": ["text", "document"], "tools": ["file_search"],
            "document_count": 3, "document_pages": 12,
            "searches_per_call": 2, "workflow_steps": 6,
        }},
    )

    assert arguments["document_count"] == 3
    assert arguments["document_pages"] == 12
    assert arguments["searches_per_call"] == 2
    assert arguments["workflow_steps"] == 6
    assert intake["confirmed_profile"]["workflow_steps"] == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"users": 0},
        {"calls_per_user_per_day": 0},
    ],
)
def test_mcp_prediction_rejects_invalid_explicit_scale(overrides):
    with pytest.raises(ValueError, match="must be at least 1"):
        McpPredictorClient(".").predict("RAG solution for 1,000 users", overrides)