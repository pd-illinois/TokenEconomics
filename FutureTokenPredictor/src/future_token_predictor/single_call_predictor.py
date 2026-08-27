"""Single-call predictor (Tier 1) — heuristic-based per-call token estimation.

Uses archetype profiles and multimodal token calculator to produce
per-invocation token estimates without historical data.
"""

from __future__ import annotations

from future_token_predictor.archetypes import get_token_profile, match_archetype
from future_token_predictor.models.schemas import (
    ImageInputProfile,
    ModalityBreakdown,
    Modality,
    UseCaseProfile,
)
from future_token_predictor.token_calculator import (
    audio_input_tokens,
    audio_output_tokens,
    image_input_tokens,
    image_output_tokens,
    reasoning_token_multiplier,
)


def predict_single_call(profile: UseCaseProfile) -> ModalityBreakdown:
    """Predict tokens for a single agent invocation using Tier 1 heuristics.

    Strategy:
    1. Match to best-fit MAF archetype
    2. Load archetype token profile at the given complexity
    3. Compute modality-specific tokens using calculator
    4. Return complete ModalityBreakdown
    """
    # Match archetype
    archetype = match_archetype(
        profile.agent_type, profile.modalities, profile.tools, profile.agent_pattern
    )
    token_profile = get_token_profile(archetype, profile.complexity)

    # Base text tokens
    system_tokens = profile.system_prompt_tokens or token_profile.get("system_prompt", 500)
    user_tokens = profile.avg_user_input_tokens or token_profile.get("user_input", 200)
    output_mean = token_profile.get("output_mean", 400)

    breakdown = ModalityBreakdown()
    breakdown.text_input = float(system_tokens + user_tokens)
    breakdown.text_output = float(output_mean)

    # Image input tokens (vision)
    if Modality.IMAGE_INPUT in profile.modalities:
        if profile.image_inputs:
            breakdown.image_input = float(image_input_tokens(profile.image_inputs))
        else:
            # Use archetype defaults
            img_profile = ImageInputProfile(
                count_per_call=token_profile.get("images_per_call", 1),
                avg_width=token_profile.get("avg_image_width", 1024),
                avg_height=token_profile.get("avg_image_height", 1024),
            )
            breakdown.image_input = float(image_input_tokens(img_profile))

    # Image output tokens (generation)
    if Modality.IMAGE_OUTPUT in profile.modalities:
        images_gen = token_profile.get("images_generated", 1)
        breakdown.image_output = float(image_output_tokens() * images_gen)

    # Document tokens
    if Modality.DOCUMENT in profile.modalities:
        from future_token_predictor.token_calculator import document_tokens

        if profile.document_inputs:
            breakdown.document_input = float(document_tokens(profile.document_inputs))
        else:
            from future_token_predictor.models.schemas import (
                DocumentInputProfile,
                RetrievalStrategy,
            )
            chunks = token_profile.get("chunks_per_search", 5)
            doc_profile = DocumentInputProfile(
                retrieval_strategy=RetrievalStrategy.FILE_SEARCH,
                top_k=chunks,
            )
            breakdown.document_input = float(document_tokens(doc_profile))

    # Audio tokens
    if Modality.AUDIO_INPUT in profile.modalities:
        if profile.audio_inputs:
            breakdown.audio_input = float(audio_input_tokens(profile.audio_inputs))
        else:
            from future_token_predictor.models.schemas import AudioInputProfile
            secs = token_profile.get("audio_input_seconds", 30)
            breakdown.audio_input = float(audio_input_tokens(AudioInputProfile(avg_duration_seconds=secs)))

    if Modality.AUDIO_OUTPUT in profile.modalities:
        secs = token_profile.get("audio_output_seconds", 15)
        breakdown.audio_output = float(audio_output_tokens(secs))

    # Reasoning tokens (o-series)
    multiplier = reasoning_token_multiplier(profile.model)
    if multiplier > 1.0:
        breakdown.reasoning = breakdown.text_output * (multiplier - 1.0)

    return breakdown
