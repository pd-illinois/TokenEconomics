"""Scale projector — projects per-call costs to daily/monthly/annual estimates.

Accounts for user count, call frequency, and prompt caching discounts.
"""

from __future__ import annotations

from dataclasses import dataclass

from future_token_predictor.models.schemas import (
    CostEstimate,
    ModalityBreakdown,
    ToolCostBreakdown,
    UseCaseProfile,
)


# Prompt caching discount: shared system prompts across users in the same
# session window are cached. Typically 60-80% of system prompt tokens get
# cached after the first call.
CACHE_HIT_RATE = 0.75  # 75% of system prompt cached on subsequent calls


@dataclass
class ScaledProjection:
    """Scaled cost projections over time periods."""

    daily_calls: int
    daily_tokens: float
    daily_cost: CostEstimate
    monthly_cost: CostEstimate
    annual_cost: CostEstimate
    daily_tool_cost_usd: float
    monthly_tool_cost_usd: float
    cache_savings_pct: float
    daily_cache_savings_usd: float
    cache_discount_factor: float


def project_scale(
    profile: UseCaseProfile,
    tokens_per_call: ModalityBreakdown,
    cost_per_call_usd: float,
    cost_ci_low: float,
    cost_ci_high: float,
    cost_worst: float,
    tool_costs: ToolCostBreakdown,
    input_price_per_million: float | None = None,
    cached_input_price_per_million: float | None = None,
) -> ScaledProjection:
    """Project costs across the specified user base and frequency.

    Applies prompt caching discount for repeated calls with the same
    system prompt.
    """
    daily_calls = profile.users * profile.calls_per_user_per_day

    # Prompt caching savings: first call per user is full price,
    # subsequent calls benefit from cached system prompt
    calls_with_cache = max(0, profile.calls_per_user_per_day - 1) * profile.users
    first_calls = profile.users  # 1 uncached call per user per day

    # Calculate effective cache discount
    system_tokens = tokens_per_call.text_input * 0.6  # ~60% is system prompt
    cached_savings_per_call = system_tokens * CACHE_HIT_RATE
    total_tokens_per_day = tokens_per_call.total * daily_calls

    # Reduce tokens for cached calls (only reduces text_input cost)
    effective_daily_tokens = total_tokens_per_day - (cached_savings_per_call * calls_with_cache)
    cache_ratio = cached_savings_per_call * calls_with_cache / total_tokens_per_day if total_tokens_per_day > 0 else 0

    # Scale costs
    full_cost_daily = cost_per_call_usd * daily_calls
    if input_price_per_million is not None and cached_input_price_per_million is not None:
        savings_per_cached_call = (
            cached_savings_per_call
            * (input_price_per_million - cached_input_price_per_million)
            / 1_000_000
        )
        daily_cache_savings = savings_per_cached_call * calls_with_cache
        effective_daily_cost = max(0.0, full_cost_daily - daily_cache_savings)
        cache_discount_factor = (
            effective_daily_cost / full_cost_daily if full_cost_daily > 0 else 1.0
        )
    else:
        cache_discount_factor = 1.0 - (cache_ratio * 0.5)
        effective_daily_cost = full_cost_daily * cache_discount_factor
        daily_cache_savings = full_cost_daily - effective_daily_cost

    daily_cost = CostEstimate(
        mean=effective_daily_cost,
        ci_95_low=cost_ci_low * daily_calls * cache_discount_factor,
        ci_95_high=cost_ci_high * daily_calls * cache_discount_factor,
        worst_case=cost_worst * daily_calls * cache_discount_factor,
    )

    monthly_cost = CostEstimate(
        mean=daily_cost.mean * 30,
        ci_95_low=daily_cost.ci_95_low * 30,
        ci_95_high=daily_cost.ci_95_high * 30,
        worst_case=daily_cost.worst_case * 30,
    )

    annual_cost = CostEstimate(
        mean=daily_cost.mean * 365,
        ci_95_low=daily_cost.ci_95_low * 365,
        ci_95_high=daily_cost.ci_95_high * 365,
        worst_case=daily_cost.worst_case * 365,
    )

    # Tool costs scale linearly with calls
    daily_tool = tool_costs.total_usd * daily_calls + tool_costs.storage_cost_usd_per_day
    monthly_tool = daily_tool * 30

    return ScaledProjection(
        daily_calls=daily_calls,
        daily_tokens=effective_daily_tokens,
        daily_cost=daily_cost,
        monthly_cost=monthly_cost,
        annual_cost=annual_cost,
        daily_tool_cost_usd=daily_tool,
        monthly_tool_cost_usd=monthly_tool,
        cache_savings_pct=cache_ratio * 100,
        daily_cache_savings_usd=daily_cache_savings,
        cache_discount_factor=cache_discount_factor,
    )
