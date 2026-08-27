"""Provider registry — maps Provider enum to provider implementations."""

from __future__ import annotations

from typing import Optional

from future_token_predictor.models.schemas import Provider
from future_token_predictor.providers.base import BaseProvider

_registry: dict[Provider, BaseProvider] = {}


def register_provider(provider_id: Provider, instance: BaseProvider) -> None:
    """Register a provider implementation."""
    _registry[provider_id] = instance


def get_provider(provider_id: Provider) -> BaseProvider:
    """Get a registered provider. Raises KeyError if not found."""
    if provider_id not in _registry:
        _init_default_providers()
    if provider_id not in _registry:
        raise KeyError(f"Provider not registered: {provider_id.value}")
    return _registry[provider_id]


def list_providers() -> list[Provider]:
    """List all registered provider IDs."""
    if not _registry:
        _init_default_providers()
    return list(_registry.keys())


def resolve_provider_for_model(model: str) -> Optional[tuple[Provider, BaseProvider]]:
    """Find which provider supports a given model name."""
    if not _registry:
        _init_default_providers()
    for pid, prov in _registry.items():
        if prov.supports_model(model):
            return pid, prov
    return None


def _init_default_providers() -> None:
    """Lazy-load all built-in providers on first access."""
    if _registry:
        return

    from future_token_predictor.providers.openai_provider import OpenAIProvider
    from future_token_predictor.providers.anthropic_provider import AnthropicProvider
    from future_token_predictor.providers.google_provider import GoogleProvider
    from future_token_predictor.providers.mistral_provider import MistralProvider
    from future_token_predictor.providers.cohere_provider import CohereProvider
    from future_token_predictor.providers.bedrock_provider import BedrockProvider
    from future_token_predictor.providers.local_provider import LocalProvider

    openai_prov = OpenAIProvider()
    register_provider(Provider.OPENAI, openai_prov)
    register_provider(Provider.AZURE_OPENAI, openai_prov)  # Same models/formulas
    register_provider(Provider.ANTHROPIC, AnthropicProvider())
    register_provider(Provider.GOOGLE, GoogleProvider())
    register_provider(Provider.MISTRAL, MistralProvider())
    register_provider(Provider.COHERE, CohereProvider())
    register_provider(Provider.BEDROCK, BedrockProvider())
    register_provider(Provider.LOCAL, LocalProvider())
