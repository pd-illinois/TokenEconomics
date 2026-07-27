"""Tool cost estimator — estimates non-token costs for MAF built-in tools.

Covers File Search, Code Interpreter, and Web Search per-call/session costs.
"""

from __future__ import annotations

from future_token_predictor.archetypes import get_token_profile, match_archetype
from future_token_predictor.azure_pricing import get_pricing_client
from future_token_predictor.models.schemas import (
    Tool,
    ToolCostBreakdown,
    UseCaseProfile,
)


def estimate_tool_costs(profile: UseCaseProfile) -> ToolCostBreakdown:
    """Estimate per-invocation tool costs based on the use case profile.

    Tool costs are fixed-price additions independent of token consumption:
    - File Search: $2.50 per 1,000 calls + $0.11/GB/day storage
    - Code Interpreter: $0.033 per session (1-hour window)
    - Web Search: varies (estimated)
    """
    pricing = get_pricing_client().get_tool_pricing()
    breakdown = ToolCostBreakdown()

    # Get archetype for tool call frequency estimates
    archetype = match_archetype(
        profile.agent_type, profile.modalities, profile.tools, profile.agent_pattern
    )
    token_profile = get_token_profile(archetype, profile.complexity)

    # File Search
    if Tool.FILE_SEARCH in profile.tools:
        calls = profile.searches_per_call or token_profile.get("file_search_calls", 1)
        # For workflow/hosted agents, multiply by expected iterations
        if profile.agent_type.value != "prompt" and profile.searches_per_call is None:
            iterations = token_profile.get(
                "iterations_mean",
                token_profile.get("steps_mean", 3),
            )
            calls = int(calls * iterations)

        breakdown.file_search_calls = calls
        cost_per_call = pricing["file_search_per_1k_calls"] / 1000
        breakdown.file_search_cost_usd = calls * cost_per_call

        # Storage cost estimate (assume 100MB per knowledge base)
        breakdown.storage_gb = 0.1
        breakdown.storage_cost_usd_per_day = (
            breakdown.storage_gb * pricing["file_search_storage_per_gb_day"]
        )

    # Code Interpreter
    if Tool.CODE_INTERPRETER in profile.tools:
        sessions = token_profile.get("sessions", 1)
        code_iterations = token_profile.get("code_gen_iterations", 1)
        # Multiple code executions within 1 hour = 1 session
        # Assume each call gets 1 session unless very complex
        breakdown.code_interpreter_sessions = sessions
        breakdown.code_interpreter_cost_usd = (
            sessions * pricing["code_interpreter_per_session"]
        )

    # Web Search (estimated — pricing may vary)
    if Tool.WEB_SEARCH in profile.tools:
        # Estimate 1-3 web searches per invocation based on complexity
        complexity_calls = {"low": 1, "medium": 2, "high": 3}
        calls = profile.searches_per_call or complexity_calls.get(profile.complexity.value, 2)
        breakdown.web_search_calls = calls
        # Bing grounding cost estimate: ~$0.005 per search (approximate)
        breakdown.web_search_cost_usd = calls * 0.005

    for tool in (Tool.CUSTOM_FUNCTION, Tool.MCP_SERVER, Tool.FUNCTION_CALLING):
        if tool in profile.tools:
            breakdown.unpriced_external_tools.append({
                "tool": tool.value,
                "pricing_status": "externally_billed_unpriced",
            })

    return breakdown
