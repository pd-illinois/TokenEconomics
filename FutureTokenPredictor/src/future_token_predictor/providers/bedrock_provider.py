"""AWS Bedrock provider implementation.

Wraps models from multiple providers (Anthropic Claude, Meta Llama, Mistral)
as accessed through the Bedrock API. Pricing reflects Bedrock's rates.
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
    "bedrock-claude-sonnet-4": ModelInfo(
        name="bedrock-claude-sonnet-4", provider="bedrock",
        context_window=200_000, max_output_tokens=16_000,
        supports_vision=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "bedrock-claude-haiku-3.5": ModelInfo(
        name="bedrock-claude-haiku-3.5", provider="bedrock",
        context_window=200_000, max_output_tokens=8_192,
        supports_vision=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "bedrock-llama-3.1-70b": ModelInfo(
        name="bedrock-llama-3.1-70b", provider="bedrock",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "bedrock-llama-3.1-8b": ModelInfo(
        name="bedrock-llama-3.1-8b", provider="bedrock",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "bedrock-mistral-large": ModelInfo(
        name="bedrock-mistral-large", provider="bedrock",
        context_window=128_000, max_output_tokens=8_192,
        tokenizer="cl100k_base",
    ),
}

# Bedrock pricing — Claude models at Anthropic parity (current AWS pricing).
# Llama/Mistral reflect Bedrock-specific on-demand rates.
_PRICING: dict[str, PricingTier] = {
    "bedrock-claude-sonnet-4": PricingTier(
        input=3.00, output=15.00, cached_input=0.30,
    ),
    "bedrock-claude-haiku-3.5": PricingTier(
        input=0.80, output=4.00, cached_input=0.08,
    ),
    "bedrock-llama-3.1-70b": PricingTier(input=2.65, output=3.50),
    "bedrock-llama-3.1-8b": PricingTier(input=0.30, output=0.60),
    "bedrock-mistral-large": PricingTier(input=4.00, output=12.00),
}

# Reuse Anthropic's resolution tiers for Claude-on-Bedrock vision
_CLAUDE_IMAGE_TIERS: list[tuple[int, int, int]] = [
    (200, 200, 54),
    (400, 400, 170),
    (600, 400, 254),
    (800, 600, 438),
    (800, 800, 598),
    (1000, 800, 722),
    (1200, 1000, 1106),
    (1600, 1200, 1806),
]


class BedrockProvider(BaseProvider):

    @property
    def name(self) -> str:
        return "bedrock"

    @property
    def display_name(self) -> str:
        return "AWS Bedrock"

    @property
    def pricing_url(self) -> str:
        return "https://aws.amazon.com/bedrock/pricing/"

    @property
    def model_catalog_url(self) -> str:
        return "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html"

    def list_models(self) -> list[str]:
        return list(_MODELS.keys())

    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        return _MODELS.get(model)

    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        return _PRICING.get(model)

    def calculate_image_tokens(
        self, width: int, height: int, detail: str, count: int,
    ) -> ImageTokenResult:
        per_image = self._claude_tier_tokens(width, height)
        return ImageTokenResult(
            tokens_per_image=per_image,
            total_tokens=per_image * count,
            method="resolution_tier",
        )

    def _claude_tier_tokens(self, width: int, height: int) -> int:
        for max_w, max_h, tokens in _CLAUDE_IMAGE_TIERS:
            if width <= max_w and height <= max_h:
                return tokens
        return 1806

    def get_reasoning_multiplier(self, model: str) -> float:
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        return "cl100k_base"
