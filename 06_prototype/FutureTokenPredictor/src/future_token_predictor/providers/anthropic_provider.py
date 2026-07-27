"""Anthropic (Claude) provider implementation.

Supports Claude Opus 4, Sonnet 4, Sonnet 4.5, Haiku 3.5.
Uses resolution-tier-based image tokenization.
"""

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


_MODELS: dict[str, ModelInfo] = {
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

# Static pricing (USD per 1M tokens, May 2026)
# Static pricing (USD per 1M tokens, May 2026)
# Source: https://www.anthropic.com/pricing
_OPUS_PRICING = PricingTier(input=15.00, output=75.00, cached_input=1.50)
_SONNET_PRICING = PricingTier(input=3.00, output=15.00, cached_input=0.30)

_PRICING: dict[str, PricingTier] = {
    "claude-opus-4": _OPUS_PRICING,
    "claude-opus-4.1": _OPUS_PRICING,
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
        input=0.80, output=4.00, cached_input=0.08,
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
        return "https://www.anthropic.com/pricing"

    @property
    def model_catalog_url(self) -> str:
        return "https://docs.anthropic.com/en/docs/about-claude/models"

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
