"""OpenAI / Azure OpenAI provider implementation.

Supports the full GPT model lineup from Azure OpenAI:
GPT-5.5, GPT-5.4 family, GPT-5.3, GPT-5.2, GPT-5.1, GPT-5 family,
GPT-4.1 family, GPT-4o, o-series, image/video/audio models.

Uses tile-based image tokenization.
"""

from __future__ import annotations

import math
from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ImageTokenResult,
    ModelInfo,
    PricingTier,
)

# --- Constants ---

TILE_SIZE = 512
TOKENS_PER_TILE = 170
BASE_IMAGE_TOKENS = 85
LOW_DETAIL_TOKENS = 85
MAX_IMAGE_DIMENSION = 2048

AUDIO_TOKENS_PER_SECOND = 43.0


# --- Helper to reduce boilerplate ---

def _oai(
    name: str, ctx: int, out: int, *,
    vision: bool = True, audio: bool = False,
    reasoning: bool = False, caching: bool = True,
    tokenizer: str = "o200k_base", reasoning_mult: float = 1.0,
) -> ModelInfo:
    return ModelInfo(
        name=name, provider="openai",
        context_window=ctx, max_output_tokens=out,
        supports_vision=vision, supports_audio=audio,
        supports_reasoning=reasoning, supports_caching=caching,
        tokenizer=tokenizer, reasoning_multiplier=reasoning_mult,
    )


# --- Model catalog (sourced from Azure OpenAI docs, July 2025) ---

_MODELS: dict[str, ModelInfo] = {
    # ── GPT-5.6 ──
    "gpt-5.6-sol":       _oai("gpt-5.6-sol",   1_050_000, 128_000, reasoning=True),
    "gpt-5.6-terra":     _oai("gpt-5.6-terra", 1_050_000, 128_000, reasoning=True),
    "gpt-5.6-luna":      _oai("gpt-5.6-luna",  1_050_000, 128_000, reasoning=True),

    # ── GPT-5.5 ──
    "gpt-5.5":           _oai("gpt-5.5",       1_050_000, 128_000),
    "gpt-chat-latest":   _oai("gpt-chat-latest", 400_000, 128_000),

    # ── GPT-5.4 family ──
    "gpt-5.4":           _oai("gpt-5.4",       1_050_000, 128_000),
    "gpt-5.4-pro":       _oai("gpt-5.4-pro",   1_050_000, 128_000),
    "gpt-5.4-mini":      _oai("gpt-5.4-mini",    400_000, 128_000),
    "gpt-5.4-nano":      _oai("gpt-5.4-nano",    400_000, 128_000),

    # ── GPT-5.3 ──
    "gpt-5.3-codex":     _oai("gpt-5.3-codex",   400_000, 128_000),
    "gpt-5.3-chat":      _oai("gpt-5.3-chat",    128_000, 128_000),

    # ── GPT-5.2 ──
    "gpt-5.2":           _oai("gpt-5.2",         400_000, 128_000),
    "gpt-5.2-codex":     _oai("gpt-5.2-codex",   400_000, 128_000),
    "gpt-5.2-chat":      _oai("gpt-5.2-chat",    128_000, 128_000),

    # ── GPT-5.1 ──
    "gpt-5.1":           _oai("gpt-5.1",         400_000, 128_000),
    "gpt-5.1-chat":      _oai("gpt-5.1-chat",    128_000, 128_000),
    "gpt-5.1-codex":     _oai("gpt-5.1-codex",   400_000, 128_000),
    "gpt-5.1-codex-mini": _oai("gpt-5.1-codex-mini", 400_000, 128_000),
    "gpt-5.1-codex-max": _oai("gpt-5.1-codex-max", 400_000, 128_000),

    # ── GPT-5 family ──
    "gpt-5":             _oai("gpt-5",           400_000, 128_000),
    "gpt-5-pro":         _oai("gpt-5-pro",       400_000, 128_000),
    "gpt-5-mini":        _oai("gpt-5-mini",      400_000, 128_000),
    "gpt-5-nano":        _oai("gpt-5-nano",      400_000, 128_000),
    "gpt-5-chat":        _oai("gpt-5-chat",      128_000, 128_000),
    "gpt-5-codex":       _oai("gpt-5-codex",     400_000, 128_000),

    # ── GPT-4.1 family ──
    "gpt-4.1":           _oai("gpt-4.1",       1_048_576,  32_768),
    "gpt-4.1-mini":      _oai("gpt-4.1-mini",  1_048_576,  32_768),
    "gpt-4.1-nano":      _oai("gpt-4.1-nano",  1_048_576,  32_768),

    # ── GPT-4o ──
    "gpt-4o":            _oai("gpt-4o",          128_000,  16_384, audio=True),
    "gpt-4o-mini":       _oai("gpt-4o-mini",     128_000,  16_384),
    "gpt-4-turbo":       _oai("gpt-4-turbo",     128_000,   4_096),
    "gpt-4.5-preview":   _oai("gpt-4.5-preview", 128_000,  16_384),

    # ── o-series (reasoning) ──
    "o3":                _oai("o3",               200_000, 100_000, reasoning=True, reasoning_mult=5.0),
    "o3-pro":            _oai("o3-pro",           200_000, 100_000, reasoning=True, reasoning_mult=5.0),
    "o3-mini":           _oai("o3-mini",          200_000, 100_000, reasoning=True, reasoning_mult=3.0),
    "o4-mini":           _oai("o4-mini",          200_000, 100_000, reasoning=True, reasoning_mult=3.0),
    "codex-mini":        _oai("codex-mini",       200_000, 100_000, reasoning=True, reasoning_mult=3.0),
    "o1":                _oai("o1",               200_000, 100_000, reasoning=True, reasoning_mult=4.0),
    "o1-mini":           _oai("o1-mini",          128_000, 100_000, reasoning=True, reasoning_mult=3.0),

    # ── Image models ──
    "gpt-image-1":       _oai("gpt-image-1",      32_768,       0, caching=False),
    "gpt-image-1-mini":  _oai("gpt-image-1-mini",  32_768,       0, caching=False),
    "gpt-image-1.5":     _oai("gpt-image-1.5",    32_768,       0, caching=False),
    "gpt-image-2":       _oai("gpt-image-2",      32_768,       0, caching=False),

    # ── Video models ──
    "sora":              _oai("sora",              32_768,       0, vision=False, caching=False),
    "sora-2":            _oai("sora-2",            32_768,       0, vision=False, caching=False),

    # ── Audio models ──
    "gpt-audio":         _oai("gpt-audio",        128_000,  16_384, audio=True),
    "gpt-audio-mini":    _oai("gpt-audio-mini",   128_000,  16_384, audio=True),
    "gpt-audio-1.5":     _oai("gpt-audio-1.5",   128_000,  16_384, audio=True),
    "gpt-realtime":      _oai("gpt-realtime",     128_000,  16_384, audio=True),
    "gpt-realtime-mini": _oai("gpt-realtime-mini", 128_000,  16_384, audio=True),
    "gpt-realtime-1.5":  _oai("gpt-realtime-1.5", 128_000,  16_384, audio=True),
    "gpt-4o-audio":      _oai("gpt-4o-audio",     128_000,  16_384, audio=True),

    # ── Open source ──
    "gpt-oss-120b":      _oai("gpt-oss-120b",     128_000,  16_384, caching=False),
    "gpt-oss-20b":       _oai("gpt-oss-20b",      128_000,  16_384, caching=False),

    # ── Misc ──
    "computer-use-preview": _oai("computer-use-preview", 128_000, 16_384, caching=False),
}

# Static pricing fallback (USD per 1M tokens).
_PRICING: dict[str, PricingTier] = {
    # GPT-5.6 short-context Global Standard. Azure Retail Prices API,
    # effective July/August 2026.
    "gpt-5.6-sol": PricingTier(
        input=5.00, output=30.00, cached_input=0.50, cache_write=6.25,
    ),
    "gpt-5.6-terra": PricingTier(
        input=2.00, output=12.00, cached_input=0.20, cache_write=2.50,
    ),
    "gpt-5.6-luna": PricingTier(
        input=0.20, output=1.20, cached_input=0.02, cache_write=0.25,
    ),

    # GPT-5.5 short-context Global Standard
    "gpt-5.5":          PricingTier(input=5.00, output=30.00, cached_input=0.50),
    "gpt-chat-latest":  PricingTier(input=5.00, output=30.00, cached_input=0.50),

    # GPT-5.4 family
    "gpt-5.4":          PricingTier(input=2.50, output=15.00, cached_input=0.25),
    "gpt-5.4-pro":      PricingTier(input=30.00, output=180.00),
    "gpt-5.4-mini":     PricingTier(input=0.75, output=4.50, cached_input=0.075),
    "gpt-5.4-nano":     PricingTier(input=0.20, output=1.25, cached_input=0.02),

    # GPT-5.3
    "gpt-5.3-codex":    PricingTier(input=1.75, output=14.00, cached_input=0.175),
    "gpt-5.3-chat":     PricingTier(input=1.75, output=14.00, cached_input=0.175),

    # GPT-5.2
    "gpt-5.2":          PricingTier(input=1.75, output=14.00, cached_input=0.175),
    "gpt-5.2-codex":    PricingTier(input=1.75, output=14.00, cached_input=0.175),
    "gpt-5.2-chat":     PricingTier(input=1.75, output=14.00, cached_input=0.175),

    # GPT-5.1
    "gpt-5.1":          PricingTier(input=1.25, output=10.00, cached_input=0.125),
    "gpt-5.1-chat":     PricingTier(input=1.25, output=10.00, cached_input=0.125),
    "gpt-5.1-codex":    PricingTier(input=1.25, output=10.00, cached_input=0.125),
    "gpt-5.1-codex-mini": PricingTier(input=0.25, output=2.00, cached_input=0.025),
    "gpt-5.1-codex-max": PricingTier(input=1.25, output=10.00, cached_input=0.125),

    # GPT-5 family
    "gpt-5":            PricingTier(input=1.25, output=10.00, cached_input=0.125),
    "gpt-5-pro":        PricingTier(input=15.00, output=120.00),
    "gpt-5-mini":       PricingTier(input=0.25, output=2.00, cached_input=0.025),
    "gpt-5-nano":       PricingTier(input=0.05, output=0.40, cached_input=0.005),
    "gpt-5-chat":       PricingTier(input=1.25, output=10.00, cached_input=0.125),
    "gpt-5-codex":      PricingTier(input=1.25, output=10.00, cached_input=0.125),

    # GPT-4.1 family
    "gpt-4.1":          PricingTier(input=2.00, output=8.00, cached_input=0.50),
    "gpt-4.1-mini":     PricingTier(input=0.40, output=1.60, cached_input=0.10),
    "gpt-4.1-nano":     PricingTier(input=0.10, output=0.40, cached_input=0.025),

    # GPT-4o
    "gpt-4o":           PricingTier(input=2.50, output=10.00, cached_input=1.25),
    "gpt-4o-mini":      PricingTier(input=0.15, output=0.60, cached_input=0.075),
    "gpt-4-turbo":      PricingTier(input=10.00, output=30.00),
    "gpt-4.5-preview":  PricingTier(input=70.00, output=150.00, cached_input=40.00),

    # o-series
    "o3":               PricingTier(input=2.00, output=8.00, cached_input=0.50),
    "o3-pro":           PricingTier(input=20.00, output=80.00),
    "o3-mini":          PricingTier(input=1.10, output=4.40, cached_input=0.55),
    "o4-mini":          PricingTier(input=1.10, output=4.40, cached_input=0.275),
    "codex-mini":       PricingTier(input=1.50, output=6.00, cached_input=0.375),
    "o1":               PricingTier(input=15.00, output=60.00, cached_input=7.50),
    "o1-mini":          PricingTier(input=1.10, output=4.40, cached_input=0.55),

    # Image models
    "gpt-image-1":      PricingTier(input=5.00, output=40.00, image_input=10.00),
    "gpt-image-1-mini": PricingTier(input=2.00, output=8.00, image_input=2.50),
    "gpt-image-1.5":    PricingTier(input=5.00, output=10.00, image_input=8.00),

    # Audio models
    "gpt-audio":        PricingTier(input=2.50, output=10.00, audio_input=40.00, audio_output=80.00),
    "gpt-audio-mini":   PricingTier(input=0.60, output=2.40, audio_input=10.00, audio_output=20.00),
    "gpt-audio-1.5":    PricingTier(input=2.50, output=10.00, audio_input=32.00, audio_output=64.00),
    "gpt-realtime":     PricingTier(input=4.00, output=16.00, audio_input=32.00, audio_output=64.00),
    "gpt-realtime-mini": PricingTier(input=0.60, output=2.40, audio_input=10.00, audio_output=20.00),
    "gpt-realtime-1.5": PricingTier(input=4.00, output=16.00, audio_input=32.00, audio_output=64.00),
    "gpt-4o-audio":     PricingTier(input=2.50, output=10.00, audio_input=40.00, audio_output=80.00),

    # Open source
    "gpt-oss-120b":     PricingTier(input=0.15, output=0.60),
    "gpt-oss-20b":      PricingTier(input=0.07, output=0.30),

    # Computer use
    "computer-use-preview": PricingTier(input=3.00, output=12.00),
}


class OpenAIProvider(BaseProvider):
    """OpenAI and Azure OpenAI provider."""

    @property
    def name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "OpenAI"

    @property
    def pricing_url(self) -> str:
        return "https://openai.com/api/pricing/"

    @property
    def model_catalog_url(self) -> str:
        return "https://platform.openai.com/docs/models"

    def list_models(self) -> list[str]:
        static = list(_MODELS.keys())
        live = self._list_live_model_ids()
        return list(dict.fromkeys(static + live))

    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        return _MODELS.get(model) or self._get_live_model_info(model)

    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        static = _PRICING.get(model)
        if static:
            return static
        # Try Azure Retail Prices API (free, no auth)
        try:
            from future_token_predictor.providers.live_registry import fetch_azure_pricing
            for entry in fetch_azure_pricing():
                if entry.model_id == model:
                    return entry.to_pricing_tier()
        except Exception:
            pass
        return None

    def calculate_image_tokens(
        self, width: int, height: int, detail: str, count: int,
    ) -> ImageTokenResult:
        if detail == "low":
            per_image = LOW_DETAIL_TOKENS
            return ImageTokenResult(
                tokens_per_image=per_image,
                total_tokens=per_image * count,
                method="fixed_low_detail",
            )

        per_image = self._high_detail_tokens(width, height)
        return ImageTokenResult(
            tokens_per_image=per_image,
            total_tokens=per_image * count,
            method="tile_based",
        )

    def _high_detail_tokens(self, width: int, height: int) -> int:
        # Step 1: Scale to fit within 2048×2048
        if max(width, height) > MAX_IMAGE_DIMENSION:
            scale = MAX_IMAGE_DIMENSION / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # Step 2: Scale shortest side to 768
        min_side = min(width, height)
        if min_side > 768:
            scale = 768 / min_side
            width = int(width * scale)
            height = int(height * scale)

        # Step 3: Count tiles
        tiles_x = math.ceil(width / TILE_SIZE)
        tiles_y = math.ceil(height / TILE_SIZE)

        # Step 4: Calculate tokens
        return tiles_x * tiles_y * TOKENS_PER_TILE + BASE_IMAGE_TOKENS

    def get_audio_tokens_per_second(self) -> float:
        return AUDIO_TOKENS_PER_SECOND

    def get_reasoning_multiplier(self, model: str) -> float:
        info = _MODELS.get(model)
        if info and info.supports_reasoning:
            return info.reasoning_multiplier
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        info = _MODELS.get(model)
        return info.tokenizer if info else "o200k_base"
