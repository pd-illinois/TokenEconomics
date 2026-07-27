"""Abstract base class for LLM provider implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelInfo:
    """Metadata about a specific model."""

    name: str
    provider: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool = False
    supports_audio: bool = False
    supports_reasoning: bool = False
    supports_caching: bool = False
    tokenizer: str = "o200k_base"
    reasoning_multiplier: float = 1.0


@dataclass
class PricingTier:
    """Per-million-token pricing for a model."""

    input: float
    output: float
    cached_input: Optional[float] = None
    image_input: Optional[float] = None
    audio_input: Optional[float] = None
    audio_output: Optional[float] = None
    batch_input: Optional[float] = None
    batch_output: Optional[float] = None

    @property
    def effective_cached_input(self) -> float:
        return self.cached_input if self.cached_input is not None else self.input

    def to_dict(self) -> dict[str, float]:
        """Convert to dict for backward compatibility with cost_calculator."""
        d: dict[str, float] = {"input": self.input, "output": self.output}
        if self.cached_input is not None:
            d["cached_input"] = self.cached_input
        if self.image_input is not None:
            d["image_input"] = self.image_input
        if self.audio_input is not None:
            d["audio_input"] = self.audio_input
        if self.audio_output is not None:
            d["audio_output"] = self.audio_output
        if self.batch_input is not None:
            d["batch_input"] = self.batch_input
        if self.batch_output is not None:
            d["batch_output"] = self.batch_output
        return d


@dataclass
class ImageTokenResult:
    """Result of provider-specific image token calculation."""

    tokens_per_image: int
    total_tokens: int
    method: str  # e.g., "tile_based", "fixed", "resolution_tier"


class BaseProvider(ABC):
    """Abstract interface that every LLM provider must implement.

    Providers must NOT import from providers.__init__ to avoid circular imports.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'openai', 'anthropic')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name (e.g., 'OpenAI', 'Anthropic')."""
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return all model identifiers this provider supports."""
        ...

    @abstractmethod
    def get_model_info(self, model: str) -> Optional[ModelInfo]:
        """Return metadata for a specific model, or None if unknown."""
        ...

    @abstractmethod
    def get_pricing(
        self, model: str, deployment_type: str | None = None,
    ) -> Optional[PricingTier]:
        """Return per-million-token pricing for a model.

        deployment_type is provider-specific (e.g., 'standard', 'batch', 'provisioned'
        for OpenAI/Azure; ignored by most other providers).
        """
        ...

    @abstractmethod
    def get_reasoning_multiplier(self, model: str) -> float:
        """Return the reasoning token multiplier for a model (1.0 if not a reasoning model)."""
        ...

    @abstractmethod
    def get_tokenizer_name(self, model: str) -> str:
        """Return the tiktoken-compatible encoding name for a model."""
        ...

    @property
    def pricing_url(self) -> str:
        """URL to the provider's official pricing page."""
        return ""

    @property
    def model_catalog_url(self) -> str:
        """URL to the provider's official model catalog/docs page."""
        return ""

    def calculate_image_tokens(
        self,
        width: int,
        height: int,
        detail: str,
        count: int,
    ) -> ImageTokenResult:
        """Calculate image input tokens using this provider's formula.

        Default implementation raises NotImplementedError.
        Only vision-capable providers need to override this.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support image input"
        )

    def get_audio_tokens_per_second(self) -> float:
        """Return audio-to-token rate (tokens per second of audio).

        Default implementation raises NotImplementedError.
        Only audio-capable providers need to override this.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support audio input"
        )

    def supports_model(self, model: str) -> bool:
        """Check if this provider supports the given model."""
        return self.get_model_info(model) is not None

    # ─── Live model discovery helpers ──────────────────────────────

    def _get_live_model_info(self, model: str) -> Optional[ModelInfo]:
        """Check live registry for a model not found in the static catalog."""
        try:
            from future_token_predictor.providers.live_registry import fetch_live_models
            for entry in fetch_live_models(self.name):
                if entry.model_id == model:
                    return entry.to_model_info()
        except Exception:
            pass  # network failure → silent fallback
        return None

    def _list_live_model_ids(self) -> list[str]:
        """Return model IDs discovered from the live API."""
        try:
            from future_token_predictor.providers.live_registry import fetch_live_models
            return [e.model_id for e in fetch_live_models(self.name)]
        except Exception:
            return []
