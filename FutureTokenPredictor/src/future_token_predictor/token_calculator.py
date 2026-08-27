"""Multimodal token calculator — provider-aware dispatch.

Handles text (tiktoken), image input (provider-specific), image output,
document (page/chunk estimation), and audio (provider-specific) token counting.
Delegates to the registered provider's formulas when a provider is resolved.
"""

from __future__ import annotations

import math
from typing import Optional

import tiktoken

from future_token_predictor.models.schemas import (
    AudioInputProfile,
    DetailLevel,
    DocumentInputProfile,
    ImageInputProfile,
    Modality,
    Provider,
    RetrievalStrategy,
)

# --- Constants ---

# Document estimation
TOKENS_PER_PAGE_DIRECT_LOW = 500
TOKENS_PER_PAGE_DIRECT_HIGH = 800
TOKENS_PER_CHUNK = 512  # File Search default chunk size


def _get_provider(provider_id: Provider | None = None, model: str | None = None):
    """Resolve a BaseProvider instance from an explicit ID or model name."""
    from future_token_predictor.providers import get_provider, resolve_provider_for_model

    if provider_id is not None:
        return get_provider(provider_id)
    if model is not None:
        result = resolve_provider_for_model(model)
        if result:
            return result[1]
    return None


def _tokenizer_for_model(model: str, provider_id: Provider | None = None) -> str:
    """Return the tiktoken encoding name for a model."""
    prov = _get_provider(provider_id, model)
    if prov:
        return prov.get_tokenizer_name(model)
    return "o200k_base"


def text_tokens(
    text: str, model: str = "gpt-4.1", provider: Provider | None = None,
) -> int:
    """Count tokens in a text string using tiktoken.

    Uses the provider's declared tokenizer encoding.
    """
    encoding_name = _tokenizer_for_model(model, provider)
    enc = tiktoken.get_encoding(encoding_name)
    return len(enc.encode(text))


def estimate_text_tokens(char_count: int) -> int:
    """Estimate token count from character count (~4 chars/token for English)."""
    return max(1, math.ceil(char_count / 4))


def image_input_tokens(
    profile: ImageInputProfile,
    model: str = "gpt-4.1",
    provider: Provider | None = None,
) -> int:
    """Calculate image input tokens using the provider's formula.

    Dispatches to the provider's calculate_image_tokens method which
    implements provider-specific logic (OpenAI tiles, Claude resolution tiers,
    Gemini fixed tokens, Pixtral pixel tiles, etc.).
    """
    prov = _get_provider(provider, model)
    if prov:
        try:
            result = prov.calculate_image_tokens(
                width=profile.avg_width,
                height=profile.avg_height,
                detail=profile.detail_level.value,
                count=profile.count_per_call,
            )
            return result.total_tokens
        except NotImplementedError:
            pass

    # Fallback: OpenAI tile-based formula
    return _openai_image_tokens(profile)


def _openai_image_tokens(profile: ImageInputProfile) -> int:
    """Fallback OpenAI tile-based image token calculation."""
    if profile.detail_level == DetailLevel.LOW:
        return 85 * profile.count_per_call

    width, height = profile.avg_width, profile.avg_height
    if max(width, height) > 2048:
        scale = 2048 / max(width, height)
        width = int(width * scale)
        height = int(height * scale)
    if min(width, height) > 768:
        scale = 768 / min(width, height)
        width = int(width * scale)
        height = int(height * scale)

    tiles = math.ceil(width / 512) * math.ceil(height / 512)
    per_image = tiles * 170 + 85
    return per_image * profile.count_per_call


def image_output_tokens(
    width: int = 1024, height: int = 1024, quality: str = "standard",
) -> int:
    """Estimate image output tokens for GPT-Image-1.

    Image output pricing is per-token, with higher resolution = more tokens.
    """
    pixels = width * height
    base_pixels = 1024 * 1024
    base_tokens = 1100
    if quality == "hd":
        base_tokens = 1600
    return int(base_tokens * (pixels / base_pixels))


def document_tokens(profile: DocumentInputProfile) -> int:
    """Estimate tokens from document inputs.

    Two strategies:
    - DIRECT / RAG: Full text extraction, ~500-800 tokens per page
    - FILE_SEARCH: Chunked retrieval, 512 tokens per chunk × top_k
    """
    if profile.retrieval_strategy in (RetrievalStrategy.DIRECT, RetrievalStrategy.RAG):
        avg_tokens_per_page = (TOKENS_PER_PAGE_DIRECT_LOW + TOKENS_PER_PAGE_DIRECT_HIGH) // 2
        return profile.count * profile.avg_pages * avg_tokens_per_page
    # File Search: each query retrieves top_k chunks
    return profile.top_k * TOKENS_PER_CHUNK


def audio_input_tokens(
    profile: AudioInputProfile,
    model: str = "gpt-4o-audio",
    provider: Provider | None = None,
) -> int:
    """Estimate audio input tokens from duration using provider's rate."""
    prov = _get_provider(provider, model)
    if prov:
        try:
            tps = prov.get_audio_tokens_per_second()
            return int(profile.avg_duration_seconds * tps)
        except NotImplementedError:
            pass
    # Fallback: OpenAI rate (43 tokens/sec)
    return int(profile.avg_duration_seconds * 43)


def audio_output_tokens(
    duration_seconds: float,
    model: str = "gpt-4o-audio",
    provider: Provider | None = None,
) -> int:
    """Estimate audio output tokens from duration."""
    prov = _get_provider(provider, model)
    if prov:
        try:
            tps = prov.get_audio_tokens_per_second()
            return int(duration_seconds * tps)
        except NotImplementedError:
            pass
    return int(duration_seconds * 43)


def reasoning_token_multiplier(
    model: str, provider: Provider | None = None,
) -> float:
    """Get the reasoning token multiplier via the provider registry."""
    prov = _get_provider(provider, model)
    if prov:
        return prov.get_reasoning_multiplier(model)
    return 1.0


def calculate_all_modality_tokens(
    modalities: list[Modality],
    model: str = "gpt-4.1",
    provider: Provider | None = None,
    system_prompt_tokens: Optional[int] = None,
    user_input_tokens: Optional[int] = None,
    image_profile: Optional[ImageInputProfile] = None,
    document_profile: Optional[DocumentInputProfile] = None,
    audio_profile: Optional[AudioInputProfile] = None,
) -> dict[str, float]:
    """Calculate tokens across all modalities for a single call.

    Returns a dict with keys matching ModalityBreakdown fields.
    """
    result: dict[str, float] = {
        "text_input": 0.0,
        "text_output": 0.0,
        "cached_input": 0.0,
        "image_input": 0.0,
        "image_output": 0.0,
        "document_input": 0.0,
        "audio_input": 0.0,
        "audio_output": 0.0,
        "reasoning": 0.0,
    }

    # System prompt (always counts as cached after first call in a session)
    sys_tokens = system_prompt_tokens or 500
    result["text_input"] += sys_tokens

    # User input
    usr_tokens = user_input_tokens or 200
    result["text_input"] += usr_tokens

    # Text output estimate (will be refined by predictor)
    result["text_output"] = usr_tokens * 2

    # Image input
    if Modality.IMAGE_INPUT in modalities and image_profile:
        result["image_input"] = image_input_tokens(image_profile, model, provider)

    # Image output
    if Modality.IMAGE_OUTPUT in modalities:
        result["image_output"] = image_output_tokens()

    # Document
    if Modality.DOCUMENT in modalities and document_profile:
        result["document_input"] = document_tokens(document_profile)

    # Audio input
    if Modality.AUDIO_INPUT in modalities and audio_profile:
        result["audio_input"] = audio_input_tokens(audio_profile, model, provider)

    # Reasoning tokens
    multiplier = reasoning_token_multiplier(model, provider)
    if multiplier > 1.0:
        result["reasoning"] = result["text_output"] * (multiplier - 1.0)

    return result
