"""Report generator — produces structured prediction reports."""

from __future__ import annotations

from future_token_predictor.models.schemas import (
    CostEstimate,
    ModalityBreakdown,
    PredictionResult,
    ToolCostBreakdown,
)
from future_token_predictor.scale_projector import ScaledProjection

# Default citation URLs — used when model-specific URLs are unavailable
_DEFAULT_MODEL_CATALOG_URL = (
    "https://learn.microsoft.com/en-us/azure/foundry/foundry-models/"
    "concepts/models-sold-directly-by-azure"
)
_DEFAULT_PRICING_URL = "https://openai.com/api/pricing/"
_AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"


def format_report(result: PredictionResult) -> str:
    """Format a PredictionResult into a readable markdown report."""
    lines = []

    # Early exit for validation failures
    if result.prediction_method == "validation_failed":
        lines.append("# ⚠️ Model Validation Failed")
        lines.append("")
        lines.append(f"**Requested Model:** {result.model}")
        if result.provider:
            lines.append(f"**Provider:** {result.provider}")
        lines.append("")
        if result.model_warnings:
            for warn in result.model_warnings:
                lines.append(f"- {warn}")
            lines.append("")
        if result.optimizations:
            for opt in result.optimizations:
                lines.append(f"- {opt}")
            lines.append("")
        lines.append("No cost prediction was generated. Please provide a valid model name.")
        return "\n".join(lines)

    lines.append("# Token & Cost Prediction Report")
    lines.append("")

    # Model & archetype
    lines.append(f"**Model:** {result.model}")
    if hasattr(result, 'requested_model') and result.requested_model and result.requested_model != result.model:
        lines.append(f"**Requested Model:** {result.requested_model} (not found — see warnings below)")
    if hasattr(result, 'provider') and result.provider:
        lines.append(f"**Provider:** {result.provider}")
    lines.append(f"**Archetype:** {result.archetype}")
    lines.append(f"**Method:** {result.prediction_method}")
    if result.pricing_verified:
        lines.append(f"**Pricing verified:** {result.pricing_timestamp}")
    else:
        lines.append("**Pricing verified:** No — model not found in verified pricing catalogs")
    if result.prediction_id is not None:
        lines.append(f"**Prediction ID:** {result.prediction_id}")

    if result.agent_model_assignments:
        lines.append("")
        lines.append("## Per-Agent Model Assignments")
        lines.append("")
        lines.append("| Agent | Role | Provider | Model | Turn share | Model cost/invocation |")
        lines.append("|-------|------|----------|-------|------------|-----------------------|")
        for item in result.agent_model_assignments:
            lines.append(
                f"| {item['agent_id']} | {item['role'] or '—'} | {item['provider']} | "
                f"{item['model']} | {item['allocation_share']:.1%} | "
                f"${item['cost_per_invocation_usd']:.6f} |"
            )
        lines.append("")
        lines.append(
            "Turn shares are modeled allocations of the aggregate multi-agent token "
            "distribution, not measured per-agent runtime usage."
        )

    # Model validation warnings (shown prominently at the top)
    if hasattr(result, 'model_warnings') and result.model_warnings:
        lines.append("")
        lines.append("## ⚠️ Model Validation Warnings")
        lines.append("")
        for warn in result.model_warnings:
            lines.append(f"- {warn}")

    # Sources / citations — always shown
    lines.append("")
    lines.append("## 📎 Sources & Citations")
    lines.append("")
    catalog_url = result.model_catalog_url or _DEFAULT_MODEL_CATALOG_URL
    pricing_url = result.pricing_url or _DEFAULT_PRICING_URL
    lines.append(f"- **Model Catalog:** {catalog_url}")
    lines.append(f"- **Pricing Reference:** {pricing_url}")
    lines.append(f"- **Azure Retail Prices API:** {_AZURE_RETAIL_PRICES_URL}")
    lines.append(
        "- **Azure Infra Pricing:** For infrastructure costs (AI Search, Cosmos DB, etc.), "
        "ask the Azure MCP pricing tool → `@azure /pricing <service>`"
    )
    lines.append("")

    # Missing parameters — shown when defaults were assumed
    if hasattr(result, 'missing_parameters') and result.missing_parameters:
        lines.append("## ⚙️ Defaulted Parameters")
        lines.append("")
        lines.append("The following parameters were not specified and used defaults:")
        lines.append("")
        for param in result.missing_parameters:
            lines.append(f"- {param}")
        lines.append("")

    # Per-call token breakdown
    lines.append("## Per-Call Token Estimate")
    lines.append("")
    t = result.tokens_per_call
    lines.append("| Modality | Tokens |")
    lines.append("|----------|--------|")
    if t.text_input > 0:
        lines.append(f"| Text Input | {t.text_input:,.0f} |")
    if t.cached_input > 0:
        lines.append(f"| Cached Input | {t.cached_input:,.0f} |")
    if t.text_output > 0:
        lines.append(f"| Text Output | {t.text_output:,.0f} |")
    if t.image_input > 0:
        lines.append(f"| Image Input | {t.image_input:,.0f} |")
    if t.image_output > 0:
        lines.append(f"| Image Output | {t.image_output:,.0f} |")
    if t.document_input > 0:
        lines.append(f"| Document Input | {t.document_input:,.0f} |")
    if t.audio_input > 0:
        lines.append(f"| Audio Input | {t.audio_input:,.0f} |")
    if t.audio_output > 0:
        lines.append(f"| Audio Output | {t.audio_output:,.0f} |")
    if t.reasoning > 0:
        lines.append(f"| Reasoning (hidden) | {t.reasoning:,.0f} |")
    lines.append(f"| **Total (mean)** | **{t.total:,.0f}** |")
    # Show token confidence range if available
    if result.tokens_p5 > 0 and result.tokens_p95 > 0:
        lines.append("")
        label = (
            "Monte Carlo P5/P95 range"
            if result.bound_method == "monte_carlo_quantile"
            else "Heuristic low/high range"
        )
        lines.append(
            f"**{label}:** {result.tokens_p5:,.0f} — {result.tokens_p95:,.0f} tokens"
        )
    lines.append("")

    # Per-call cost
    lines.append("## Per-Call Cost")
    lines.append("")
    c = result.cost_per_call
    lines.append(f"**${c.ci_95_low:.4f} — ${c.ci_95_high:.4f}** per call (mean: ${c.mean:.4f})")
    lines.append("")
    lines.append(f"- Worst case (99th pctl): ${c.worst_case:.4f}")
    lines.append("")

    # Tool costs
    tc = result.tool_costs_per_call
    if tc.total_usd > 0 or tc.unpriced_external_tools:
        lines.append("## Tool Costs (per call)")
        lines.append("")
        if tc.file_search_calls > 0:
            lines.append(f"- File Search: {tc.file_search_calls} calls → ${tc.file_search_cost_usd:.6f}")
        if tc.code_interpreter_sessions > 0:
            lines.append(f"- Code Interpreter: {tc.code_interpreter_sessions} sessions → ${tc.code_interpreter_cost_usd:.6f}")
        if tc.web_search_calls > 0:
            lines.append(f"- Web Search: {tc.web_search_calls} calls → ${tc.web_search_cost_usd:.6f}")
        if tc.storage_cost_usd_per_day > 0:
            lines.append(f"- Storage: {tc.storage_gb:.2f} GB → ${tc.storage_cost_usd_per_day:.4f}/day")
        for item in tc.unpriced_external_tools:
            lines.append(
                f"- {item['tool']}: externally billed; price not supplied or included"
            )
        lines.append(f"- **Total tool cost/call:** ${tc.total_usd:.6f}")
        if tc.unpriced_external_tools:
            lines.append("- **Coverage:** incomplete because external tool charges are unpriced")
        lines.append("")

    # Scaled projections
    lines.append("## Scaled Projections")
    lines.append("")
    lines.append(f"| Period | Low (5th pctl) | Mean | High (95th pctl) |")
    lines.append(f"|--------|---------------|------|------------------|")
    lines.append(f"| Daily  | ${result.daily_cost_usd.ci_95_low:.2f} | ${result.daily_cost_usd.mean:.2f} | ${result.daily_cost_usd.ci_95_high:.2f} |")
    lines.append(f"| Monthly | ${result.monthly_cost_usd.ci_95_low:.2f} | ${result.monthly_cost_usd.mean:.2f} | ${result.monthly_cost_usd.ci_95_high:.2f} |")
    lines.append(f"| Annual | ${result.annual_cost_usd.ci_95_low:.2f} | ${result.annual_cost_usd.mean:.2f} | ${result.annual_cost_usd.ci_95_high:.2f} |")
    lines.append("")
    lines.append(f"*Daily tokens: {result.daily_tokens:,.0f} | Worst-case monthly: ${result.monthly_cost_usd.worst_case:.2f}*")
    lines.append("")

    # Optimizations
    if result.optimizations:
        lines.append("## Optimization Suggestions")
        lines.append("")
        for opt in result.optimizations:
            lines.append(f"- {opt}")
        lines.append("")

    return "\n".join(lines)


def build_result(
    tokens: ModalityBreakdown,
    cost: CostEstimate,
    tool_costs: ToolCostBreakdown,
    projection: ScaledProjection,
    model: str,
    archetype: str,
    pricing_timestamp: str | None,
    provider: str | None = None,
    profile_summary: dict | None = None,
    pricing_verified: bool = True,
    prediction_method: str = "tier1_heuristic",
    tokens_p5: float = 0.0,
    tokens_p50: float = 0.0,
    tokens_p95: float = 0.0,
    model_warnings: list[str] | None = None,
    requested_model: str | None = None,
    pricing_url: str = "",
    model_catalog_url: str = "",
    missing_parameters: list[str] | None = None,
    prediction_id: int | None = None,
    bound_method: str = "heuristic_multiplier",
    bound_samples: int = 1,
    bound_seed: int | None = None,
    calculation_trace: dict | None = None,
    agent_model_assignments: list[dict] | None = None,
) -> PredictionResult:
    """Assemble a complete PredictionResult from component predictions."""
    # Generate optimization suggestions
    optimizations = _suggest_optimizations(tokens, model, tool_costs)

    result = PredictionResult(
        tokens_per_call=tokens,
        tokens_p5=tokens_p5,
        tokens_p50=tokens_p50,
        tokens_p95=tokens_p95,
        cost_per_call=cost,
        tool_costs_per_call=tool_costs,
        daily_tokens=projection.daily_tokens,
        daily_cost_usd=projection.daily_cost,
        monthly_cost_usd=projection.monthly_cost,
        annual_cost_usd=projection.annual_cost,
        model=model,
        archetype=archetype,
        prediction_method=prediction_method,
        pricing_verified=pricing_verified,
        pricing_timestamp=pricing_timestamp,
        optimizations=optimizations,
        model_warnings=model_warnings or [],
        requested_model=requested_model,
        pricing_url=pricing_url,
        model_catalog_url=model_catalog_url,
        missing_parameters=missing_parameters or [],
        prediction_id=prediction_id,
        bound_method=bound_method,
        bound_samples=bound_samples,
        bound_seed=bound_seed,
        agent_model_assignments=agent_model_assignments or [],
        calculation_trace=calculation_trace or {},
    )
    if provider:
        result.provider = provider
    return result


def _suggest_optimizations(
    tokens: ModalityBreakdown,
    model: str,
    tool_costs: ToolCostBreakdown,
) -> list[str]:
    """Generate cost optimization suggestions based on the prediction."""
    suggestions = []

    # Model downgrade suggestions
    if model in ("gpt-4.1", "gpt-4o", "gpt-5") and tokens.total < 2000:
        suggestions.append(
            f"Consider gpt-4.1-mini or gpt-4.1-nano for this workload — "
            f"tokens/call ({tokens.total:.0f}) suggests a simpler model may suffice."
        )

    # Image detail level
    if tokens.image_input > 2000:
        suggestions.append(
            "Consider low-detail mode for images where high fidelity isn't needed — "
            "reduces image tokens from hundreds to 85 fixed."
        )

    # Prompt caching
    if tokens.text_input > 1000:
        suggestions.append(
            "Enable prompt caching for repeated system prompts — "
            "cached input tokens are 50-75% cheaper."
        )

    # Batch API
    if tokens.total > 5000:
        suggestions.append(
            "For non-real-time workloads, batch API offers 50% cost reduction "
            "(24-hour turnaround, available from OpenAI and Anthropic)."
        )
    # File Search optimization
    if tool_costs.file_search_calls > 10:
        suggestions.append(
            "High File Search call volume — consider pre-filtering queries or "
            "reducing top_k to lower per-call token injection."
        )

    return suggestions


def _cost_to_dict(cost: CostEstimate) -> dict:
    """Serialize a CostEstimate to a JSON-safe dict."""
    return {
        "mean": round(cost.mean, 6),
        "range_low": round(cost.ci_95_low, 6),
        "range_high": round(cost.ci_95_high, 6),
        "modeled_high": round(cost.worst_case, 6),
    }


def build_json_output(result: PredictionResult) -> dict:
    """Serialize a PredictionResult to a structured JSON-friendly dict."""
    t = result.tokens_per_call
    tc = result.tool_costs_per_call
    return {
        "prediction_id": result.prediction_id,
        "model": result.model,
        "provider": result.provider,
        "archetype": result.archetype,
        "prediction_method": result.prediction_method,
        "pricing_verified": result.pricing_verified,
        "pricing_timestamp": result.pricing_timestamp,
        "requested_model": result.requested_model,
        "tokens_per_call": {
            "text_input": round(t.text_input),
            "text_output": round(t.text_output),
            "cached_input": round(t.cached_input),
            "image_input": round(t.image_input),
            "image_output": round(t.image_output),
            "document_input": round(t.document_input),
            "audio_input": round(t.audio_input),
            "audio_output": round(t.audio_output),
            "reasoning": round(t.reasoning),
            "total": round(t.total),
        },
        "tokens_p5": round(result.tokens_p5),
        "tokens_p50": round(result.tokens_p50),
        "tokens_p95": round(result.tokens_p95),
        "bound_provenance": {
            "method": result.bound_method,
            "samples": result.bound_samples,
            "seed": result.bound_seed,
        },
        "agent_model_assignments": [
            {
                key: value
                for key, value in item.items()
                if key != "components"
            }
            for item in result.agent_model_assignments
        ],
        "calculation_trace": result.calculation_trace,
        "cost_per_call": _cost_to_dict(result.cost_per_call),
        "tool_costs_per_call": {
            "file_search_calls": tc.file_search_calls,
            "file_search_cost_usd": round(tc.file_search_cost_usd, 6),
            "code_interpreter_sessions": tc.code_interpreter_sessions,
            "code_interpreter_cost_usd": round(tc.code_interpreter_cost_usd, 6),
            "web_search_calls": tc.web_search_calls,
            "web_search_cost_usd": round(tc.web_search_cost_usd, 6),
            "unpriced_external_tools": tc.unpriced_external_tools,
            "is_complete": not bool(tc.unpriced_external_tools),
            "total_usd": round(tc.total_usd, 6),
        },
        "daily_tokens": round(result.daily_tokens),
        "daily_cost": _cost_to_dict(result.daily_cost_usd),
        "monthly_cost": _cost_to_dict(result.monthly_cost_usd),
        "annual_cost": _cost_to_dict(result.annual_cost_usd),
        "optimizations": result.optimizations,
        "model_warnings": result.model_warnings,
        "missing_parameters": result.missing_parameters,
        "sources": {
            "model_catalog_url": result.model_catalog_url or _DEFAULT_MODEL_CATALOG_URL,
            "pricing_url": result.pricing_url or _DEFAULT_PRICING_URL,
            "azure_retail_prices_api": _AZURE_RETAIL_PRICES_URL,
        },
    }
