"""Mistral provider implementation.

Supports Mistral Large, Medium, Small, and Codestral.
Vision support via Pixtral models.
"""

from __future__ import annotations

from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ImageTokenResult,
    ModelInfo,
    PricingTier,
)

_MODELS: dict[str, ModelInfo] = {
    "mistral-large": ModelInfo(
        name="mistral-large", provider="mistral",
        context_window=128_000, max_output_tokens=8_192,
        supports_vision=True, tokenizer="cl100k_base",
    ),
    "mistral-small": ModelInfo(
        name="mistral-small", provider="mistral",
        context_window=128_000, max_output_tokens=8_192,
        tokenizer="cl100k_base",
    ),
    "codestral": ModelInfo(
        name="codestral", provider="mistral",
        context_window=256_000, max_output_tokens=8_192,
        tokenizer="cl100k_base",
    ),
    "pixtral-large": ModelInfo(
        name="pixtral-large", provider="mistral",
        context_window=128_000, max_output_tokens=8_192,
        supports_vision=True, tokenizer="cl100k_base",
    ),
}

_PRICING: dict[str, PricingTier] = {
    "mistral-large": PricingTier(input=2.00, output=6.00),
    "mistral-small": PricingTier(input=0.10, output=0.30),
    "codestral": PricingTier(input=0.30, output=0.90),
    "pixtral-large": PricingTier(input=2.00, output=6.00),
}

# Mistral Pixtral: ~1 token per 16×16 pixel tile (estimated)
_PIXTRAL_TILE_SIZE = 16
_PIXTRAL_TOKENS_PER_TILE = 1


class MistralProvider(BaseProvider):

    @property
    def name(self) -> str:
        return "mistral"

    @property
    def display_name(self) -> str:
        return "Mistral"

    @property
    def pricing_url(self) -> str:
        return "https://mistral.ai/products/la-plateforme#pricing"

    @property
    def model_catalog_url(self) -> str:
        return "https://docs.mistral.ai/getting-started/models/models_overview/"

    def list_models(self) -> list[str]:
        static = list(_MODELS.keys())
        live = self._list_live_model_ids()
        return list(dict.fromkeys(static + live))

    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        return _MODELS.get(model) or self._get_live_model_info(model)

    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        return _PRICING.get(model)

    def calculate_image_tokens(
        self, width: int, height: int, detail: str, count: int,
    ) -> ImageTokenResult:
        import math
        tiles_x = math.ceil(width / _PIXTRAL_TILE_SIZE)
        tiles_y = math.ceil(height / _PIXTRAL_TILE_SIZE)
        per_image = tiles_x * tiles_y * _PIXTRAL_TOKENS_PER_TILE
        return ImageTokenResult(
            tokens_per_image=per_image,
            total_tokens=per_image * count,
            method="pixtral_tile",
        )

    def get_reasoning_multiplier(self, model: str) -> float:
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        return "cl100k_base"
