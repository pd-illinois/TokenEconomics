"""Cohere provider implementation.

Supports Command R and Command R+ models.
No vision or audio support.
"""

from __future__ import annotations

from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ModelInfo,
    PricingTier,
)

_MODELS: dict[str, ModelInfo] = {
    "command-r-plus": ModelInfo(
        name="command-r-plus", provider="cohere",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "command-r": ModelInfo(
        name="command-r", provider="cohere",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "command-a": ModelInfo(
        name="command-a", provider="cohere",
        context_window=256_000, max_output_tokens=8_192,
        tokenizer="cl100k_base",
    ),
}

_PRICING: dict[str, PricingTier] = {
    "command-r-plus": PricingTier(input=2.50, output=10.00),
    "command-r": PricingTier(input=0.15, output=0.60),
    "command-a": PricingTier(input=2.50, output=10.00),
}


class CohereProvider(BaseProvider):

    @property
    def name(self) -> str:
        return "cohere"

    @property
    def display_name(self) -> str:
        return "Cohere"

    @property
    def pricing_url(self) -> str:
        return "https://cohere.com/pricing"

    @property
    def model_catalog_url(self) -> str:
        return "https://docs.cohere.com/docs/models"

    def list_models(self) -> list[str]:
        return list(_MODELS.keys())

    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        return _MODELS.get(model)

    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        return _PRICING.get(model)

    def get_reasoning_multiplier(self, model: str) -> float:
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        return "cl100k_base"
