"""Anthropic (Claude) provider implementation."""

from __future__ import annotations

from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ImageTokenResult,
    ModelInfo,
    PricingTier,
)

# --- Image token tiers (from Anthropic vision docs) ---
# Tokens are calculated based on which size bucket the image fits into.
# Images are scaled to fit within these constraints.
_IMAGE_TIERS: list[tuple[int, int, int]] = [
    # (max_width, max_height, tokens)
    (200, 200, 54),
    (400, 400, 170),
    (600, 400, 254),
    (800, 600, 438),
    (800, 800, 598),
    (1000, 800, 722),
    (1200, 1000, 1106),
    (1600, 1200, 1806),
]
_MAX_IMAGE_TOKENS = 1806  # Largest tier


# --- Model catalog ---

def _opus_info(version: str) -> ModelInfo:
    return ModelInfo(
        name=f"claude-opus-{version}", provider="anthropic",
        context_window=200_000, max_output_tokens=32_000,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base", reasoning_multiplier=1.0,
    )


def _sonnet_info(version: str) -> ModelInfo:
    return ModelInfo(
        name=f"claude-sonnet-{version}", provider="anthropic",
        context_window=200_000, max_output_tokens=16_000,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base", reasoning_multiplier=1.0,
    )


def _foundry_info(name: str, context: int, output: int) -> ModelInfo:
    return ModelInfo(
        name=name, provider="anthropic",
        context_window=context, max_output_tokens=output,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base", reasoning_multiplier=1.0,
    )


_MODELS: dict[str, ModelInfo] = {
    "claude-mythos-5": _foundry_info("claude-mythos-5", 1_000_000, 128_000),
    "claude-fable-5": _foundry_info("claude-fable-5", 1_000_000, 128_000),
    "claude-mythos-preview": _foundry_info(
        "claude-mythos-preview", 1_000_000, 128_000,
    ),
    "claude-opus-5": ModelInfo(
        name="claude-opus-5", provider="anthropic",
        context_window=1_000_000, max_output_tokens=128_000,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "claude-sonnet-5": ModelInfo(
        name="claude-sonnet-5", provider="anthropic",
        context_window=1_000_000, max_output_tokens=128_000,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "claude-haiku-4-5": ModelInfo(
        name="claude-haiku-4-5", provider="anthropic",
        context_window=200_000, max_output_tokens=64_000,
        supports_vision=True, supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "claude-opus-4-8": _foundry_info("claude-opus-4-8", 1_000_000, 128_000),
    "claude-opus-4-7": _foundry_info("claude-opus-4-7", 1_000_000, 128_000),
    "claude-opus-4-6": _foundry_info("claude-opus-4-6", 1_000_000, 128_000),
    "claude-opus-4-5": _foundry_info("claude-opus-4-5", 200_000, 64_000),
    "claude-sonnet-4-6": _foundry_info(
        "claude-sonnet-4-6", 1_000_000, 128_000,
    ),
    "claude-sonnet-4-5": _foundry_info("claude-sonnet-4-5", 200_000, 64_000),
    "claude-opus-4": _opus_info("4"),
    "claude-opus-4.1": _opus_info("4.1"),
    "claude-opus-4.5": _opus_info("4.5"),
    "claude-opus-4.6": _opus_info("4.6"),
    "claude-opus-4.7": _opus_info("4.7"),
    "claude-sonnet-4": _sonnet_info("4"),
    "claude-sonnet-4.5": _sonnet_info("4.5"),
    "claude-sonnet-4.6": _sonnet_info("4.6"),
    "claude-haiku-3.5": ModelInfo(
        name="claude-haiku-3.5", provider="anthropic",
        context_window=200_000, max_output_tokens=8_192,
        supports_vision=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
    "claude-haiku-4.5": ModelInfo(
        name="claude-haiku-4.5", provider="anthropic",
        context_window=200_000, max_output_tokens=8_192,
        supports_vision=True, supports_caching=True,
        tokenizer="cl100k_base",
    ),
}

# Static pricing (USD per 1M tokens).
# Source: https://platform.claude.com/docs/en/about-claude/pricing
_OPUS_LEGACY_PRICING = PricingTier(input=15.00, output=75.00, cached_input=1.50)
_OPUS_PRICING = PricingTier(
    input=5.00, output=25.00, cached_input=0.50,
    cache_write_5m=6.25, cache_write_1h=10.00,
)
_SONNET_PRICING = PricingTier(
    input=3.00, output=15.00, cached_input=0.30,
    cache_write_5m=3.75, cache_write_1h=6.00,
)
_FABLE_PRICING = PricingTier(
    input=10.00, output=50.00, cached_input=1.00,
    cache_write_5m=12.50, cache_write_1h=20.00,
)

_PRICING: dict[str, PricingTier] = {
    "claude-mythos-5": _FABLE_PRICING,
    "claude-fable-5": _FABLE_PRICING,
    "claude-opus-5": _OPUS_PRICING,
    "claude-sonnet-5": PricingTier(
        input=2.00, output=10.00, cached_input=0.20,
        cache_write_5m=2.50, cache_write_1h=4.00,
    ),
    "claude-haiku-4-5": PricingTier(
        input=1.00, output=5.00, cached_input=0.10,
        cache_write_5m=1.25, cache_write_1h=2.00,
    ),
    "claude-opus-4-8": _OPUS_PRICING,
    "claude-opus-4-7": _OPUS_PRICING,
    "claude-opus-4-6": _OPUS_PRICING,
    "claude-opus-4-5": _OPUS_PRICING,
    "claude-sonnet-4-6": _SONNET_PRICING,
    "claude-sonnet-4-5": _SONNET_PRICING,
    "claude-opus-4": _OPUS_LEGACY_PRICING,
    "claude-opus-4.1": _OPUS_LEGACY_PRICING,
    "claude-opus-4.5": _OPUS_PRICING,
    "claude-opus-4.6": _OPUS_PRICING,
    "claude-opus-4.7": _OPUS_PRICING,
    "claude-sonnet-4": _SONNET_PRICING,
    "claude-sonnet-4.5": _SONNET_PRICING,
    "claude-sonnet-4.6": _SONNET_PRICING,
    "claude-haiku-3.5": PricingTier(
        input=0.80, output=4.00, cached_input=0.08,
    ),
    "claude-haiku-4.5": PricingTier(
        input=1.00, output=5.00, cached_input=0.10,
        cache_write_5m=1.25, cache_write_1h=2.00,
    ),
}


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider."""

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic"

    @property
    def pricing_url(self) -> str:
        return "https://platform.claude.com/docs/en/about-claude/pricing"

    @property
    def model_catalog_url(self) -> str:
        return (
            "https://learn.microsoft.com/azure/foundry/foundry-models/"
            "concepts/claude-models"
        )

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
        per_image = self._resolution_tier_tokens(width, height)
        return ImageTokenResult(
            tokens_per_image=per_image,
            total_tokens=per_image * count,
            method="resolution_tier",
        )

    def _resolution_tier_tokens(self, width: int, height: int) -> int:
        """Match image to the smallest tier that fits."""
        for max_w, max_h, tokens in _IMAGE_TIERS:
            if width <= max_w and height <= max_h:
                return tokens
        return _MAX_IMAGE_TOKENS

    def get_reasoning_multiplier(self, model: str) -> float:
        # Claude uses explicit thinking budgets, not multipliers.
        # Return 1.0; thinking tokens are handled via thinking_budget field.
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        info = _MODELS.get(model)
        return info.tokenizer if info else "cl100k_base"
