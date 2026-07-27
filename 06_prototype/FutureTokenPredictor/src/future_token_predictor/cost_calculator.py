"""Cost calculator — computes USD costs from token predictions.

Uses provider-specific pricing from the registry, with Azure Retail Prices API
fallback for Azure/OpenAI models.
"""

from __future__ import annotations

from future_token_predictor.models.schemas import (
    CostEstimate,
    DeploymentRegion,
    ModalityBreakdown,
    Provider,
    UseCaseProfile,
)


def _get_prices(
    model: str,
    provider: Provider | None = None,
    deployment_region: DeploymentRegion = DeploymentRegion.GLOBAL,
) -> dict[str, float]:
    """Get pricing dict for a model, preferring provider registry."""
    from future_token_predictor.providers import get_provider, resolve_provider_for_model

    prov = None
    if provider is not None:
        try:
            prov = get_provider(provider)
        except KeyError:
            pass
    else:
        result = resolve_provider_for_model(model)
        if result:
            provider, prov = result

    if prov:
        pricing_tier = prov.get_pricing(model, deployment_region.value if deployment_region else None)
        if pricing_tier:
            return pricing_tier.to_dict()

    # Fallback: Azure pricing client (for Azure/OpenAI or unknown models)
    from future_token_predictor.azure_pricing import get_pricing_client
    client = get_pricing_client()
    return client.get_model_pricing(model, deployment_region)


def calculate_cost(
    tokens: ModalityBreakdown,
    model: str,
    deployment_region: DeploymentRegion = DeploymentRegion.GLOBAL,
    provider: Provider | None = None,
) -> float:
    """Calculate cost in USD for a given token breakdown.

    Uses provider-specific pricing from registry, with Azure API fallback.
    Returns cost in USD.
    """
    prices = _get_prices(model, provider, deployment_region)
    per_million = 1_000_000.0

    cost = 0.0
    cost += tokens.text_input * prices.get("input", 2.0) / per_million
    cost += tokens.cached_input * prices.get("cached_input", prices.get("input", 2.0) * 0.25) / per_million
    cost += tokens.text_output * prices.get("output", 8.0) / per_million

    image_input_price = prices.get("image_input", prices.get("input", 2.0))
    cost += tokens.image_input * image_input_price / per_million
    cost += tokens.image_output * prices.get("output", 40.0) / per_million
    cost += tokens.document_input * prices.get("input", 2.0) / per_million

    cost += tokens.audio_input * prices.get("audio_input", prices.get("input", 2.0)) / per_million
    cost += tokens.audio_output * prices.get("audio_output", prices.get("output", 8.0)) / per_million
    cost += tokens.reasoning * prices.get("output", 8.0) / per_million

    return cost


def calculate_cost_with_ci(
    tokens: ModalityBreakdown,
    model: str,
    deployment_region: DeploymentRegion = DeploymentRegion.GLOBAL,
    provider: Provider | None = None,
    p5_total: float | None = None,
    p95_total: float | None = None,
    p99_total: float | None = None,
) -> CostEstimate:
    """Calculate cost with confidence intervals."""
    mean_cost = calculate_cost(tokens, model, deployment_region, provider)

    if p5_total is not None and p95_total is not None and p99_total is not None:
        # Scale cost proportionally to token percentiles
        mean_total = tokens.total
        if mean_total > 0:
            ci_low = mean_cost * (p5_total / mean_total)
            ci_high = mean_cost * (p95_total / mean_total)
            worst = mean_cost * (p99_total / mean_total)
        else:
            ci_low = mean_cost * 0.5
            ci_high = mean_cost * 2.0
            worst = mean_cost * 3.0
    else:
        # Default uncertainty bounds
        ci_low = mean_cost * 0.6
        ci_high = mean_cost * 1.8
        worst = mean_cost * 2.5

    return CostEstimate(
        mean=mean_cost,
        ci_95_low=ci_low,
        ci_95_high=ci_high,
        worst_case=worst,
    )
