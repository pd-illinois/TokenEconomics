"""Local model provider implementation (Ollama, vLLM, etc.).

Supports common open-source models run locally.
Pricing is zero (local inference), but token counting still matters
for context window management and performance estimation.
"""

from __future__ import annotations

from typing import Optional

from future_token_predictor.providers.base import (
    BaseProvider,
    ModelInfo,
    PricingTier,
)

_MODELS: dict[str, ModelInfo] = {
    "llama-3.1-8b": ModelInfo(
        name="llama-3.1-8b", provider="local",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "llama-3.1-70b": ModelInfo(
        name="llama-3.1-70b", provider="local",
        context_window=128_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "mistral-7b": ModelInfo(
        name="mistral-7b", provider="local",
        context_window=32_000, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "phi-4": ModelInfo(
        name="phi-4", provider="local",
        context_window=16_384, max_output_tokens=4_096,
        tokenizer="cl100k_base",
    ),
    "qwen-2.5-72b": ModelInfo(
        name="qwen-2.5-72b", provider="local",
        context_window=128_000, max_output_tokens=8_192,
        tokenizer="cl100k_base",
    ),
    "deepseek-r1": ModelInfo(
        name="deepseek-r1", provider="local",
        context_window=128_000, max_output_tokens=8_192,
        supports_reasoning=True,
        tokenizer="cl100k_base", reasoning_multiplier=4.0,
    ),
}

# Local models: zero API cost (user pays compute)
_ZERO_PRICING = PricingTier(input=0.0, output=0.0)


class LocalProvider(BaseProvider):

    @property
    def name(self) -> str:
        return "local"

    @property
    def display_name(self) -> str:
        return "Local (Ollama/vLLM)"

    @property
    def pricing_url(self) -> str:
        return ""  # No API pricing — local inference

    @property
    def model_catalog_url(self) -> str:
        return "https://ollama.com/library"

    def list_models(self) -> list[str]:
        return list(_MODELS.keys())

    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        return _MODELS.get(model)

    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        if model in _MODELS:
            return _ZERO_PRICING
        return None

    def get_reasoning_multiplier(self, model: str) -> float:
        info = _MODELS.get(model)
        if info and info.supports_reasoning:
            return info.reasoning_multiplier
        return 1.0

    def get_tokenizer_name(self, model: str) -> str:
        return "cl100k_base"
