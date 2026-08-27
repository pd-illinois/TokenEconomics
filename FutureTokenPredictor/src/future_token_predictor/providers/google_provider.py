"""Google (Gemini) provider implementation.

Supports Gemini 2.5 Pro, 2.5 Flash, 2.0 Flash.
Uses fixed 258 tokens per image regardless of resolution.
"""

from __future__ import annotations

from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ImageTokenResult,
    ModelInfo,
    PricingTier,
)

# --- Constants ---

FIXED_IMAGE_TOKENS = 258  # Gemini uses fixed token count per image
AUDIO_TOKENS_PER_SECOND = 32.0  # Gemini audio tokenization rate

# --- Model catalog ---

_MODELS: dict[str, ModelInfo] = {
    "gemini-2.5-pro": ModelInfo(
        name="gemini-2.5-pro", provider="google",
        context_window=1_048_576, max_output_tokens=65_536,
        supports_vision=True, supports_audio=True,
        supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base", reasoning_multiplier=3.0,
    ),
    "gemini-2.5-flash": ModelInfo(
        name="gemini-2.5-flash", provider="google",
        context_window=1_048_576, max_output_tokens=65_536,
        supports_vision=True, supports_audio=True,
        supports_reasoning=True, supports_caching=True,
        tokenizer="cl100k_base", reasoning_multiplier=2.0,
    ),
    "gemini-2.0-flash": ModelInfo(
        name="gemini-2.0-flash", provider="google",
        context_window=1_048_576, max_output_tokens=8_192,
        supports_vision=True, supports_audio=True,
        supports_caching=True,
        tokenizer="cl100k_base",
    ),
}

# Static pricing (USD per 1M tokens, May 2026)
# Gemini 2.5 Pro has tiered pricing based on context length; using ≤200K tier
_PRICING: dict[str, PricingTier] = {
    "gemini-2.5-pro": PricingTier(
        input=1.25, output=10.00, cached_input=0.31,
        audio_input=1.00, audio_output=4.00,
    ),
    "gemini-2.5-flash": PricingTier(
        input=0.15, output=0.60, cached_input=0.04,
        audio_input=0.70, audio_output=2.00,
    ),
    "gemini-2.0-flash": PricingTier(
        input=0.10, output=0.40, cached_input=0.025,
        audio_input=0.70, audio_output=2.00,
    ),
}


class GoogleProvider(BaseProvider):
    """Google Gemini provider."""

    @property
    def name(self) -> str:
        return "google"

    @property
    def display_name(self) -> str:
        return "Google"

    @property
    def pricing_url(self) -> str:
        return "https://ai.google.dev/gemini-api/docs/pricing"

    @property
    def model_catalog_url(self) -> str:
        return "https://ai.google.dev/gemini-api/docs/models"

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
        return ImageTokenResult(
            tokens_per_image=FIXED_IMAGE_TOKENS,
            total_tokens=FIXED_IMAGE_TOKENS * count,
            method="fixed",
        )

    def get_audio_tokens_per_second(self) -> float:
        return AUDIO_TOKENS_PER_SECOND

    def get_reasoning_multiplier(self, model: str) -> float:
        info = _MODELS.get(model)
        if info and info.supports_reasoning:
            return info.reasoning_multiplier
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        info = _MODELS.get(model)
        return info.tokenizer if info else "cl100k_base"
