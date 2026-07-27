"""Serializable model offerings derived from provider pricing and capability registries."""

from __future__ import annotations

from future_token_predictor.providers import get_provider, list_providers

_PROVIDER_DISPLAY_NAMES = {
    "azure_openai": "Azure OpenAI",
    "openai": "OpenAI",
}


def build_model_catalog() -> dict:
    """Return forecastable offerings plus discovered models that lack pricing."""
    offerings: list[dict] = []
    unavailable: list[dict] = []
    for provider_id in list_providers():
        provider = get_provider(provider_id)
        for model_id in provider.list_models():
            info = provider.get_model_info(model_id)
            pricing = provider.get_pricing(model_id)
            if info is None or pricing is None:
                unavailable.append({
                    "model": model_id,
                    "provider": provider_id.value,
                    "reason": "missing_model_metadata" if info is None else "missing_pricing",
                })
                continue
            offerings.append({
                "key": f"{provider_id.value}:{model_id}",
                "model": model_id,
                "provider": provider_id.value,
                "provider_name": _PROVIDER_DISPLAY_NAMES.get(
                    provider_id.value,
                    provider.display_name,
                ),
                "pricing": pricing.to_dict(),
                "capabilities": {
                    "vision": info.supports_vision,
                    "audio": info.supports_audio,
                    "reasoning": info.supports_reasoning,
                    "caching": info.supports_caching,
                },
                "context_window": info.context_window,
                "max_output_tokens": info.max_output_tokens,
                "tokenizer": info.tokenizer,
                "reasoning_multiplier": provider.get_reasoning_multiplier(model_id),
                "pricing_url": provider.pricing_url,
                "model_catalog_url": provider.model_catalog_url,
            })
    offerings.sort(key=lambda item: (item["provider_name"].lower(), item["model"].lower()))
    unavailable.sort(key=lambda item: (item["provider"], item["model"]))
    return {
        "offerings": offerings,
        "unavailable": unavailable,
        "selection_rule": "Only provider/model offerings with metadata and pricing are forecastable.",
    }