"""Tests for the token calculator module."""

from __future__ import annotations

import pytest

from future_token_predictor.models.schemas import (
    AudioInputProfile,
    DetailLevel,
    DocumentInputProfile,
    ImageInputProfile,
    Modality,
    Provider,
    RetrievalStrategy,
)
from future_token_predictor.token_calculator import (
    audio_input_tokens,
    audio_output_tokens,
    calculate_all_modality_tokens,
    document_tokens,
    estimate_text_tokens,
    image_input_tokens,
    image_output_tokens,
    reasoning_token_multiplier,
    text_tokens,
)


# ── Text Tokens ──────────────────────────────────────────────────────────


class TestTextTokens:
    def test_basic_tokenization(self):
        count = text_tokens("Hello, world!")
        assert count > 0
        assert count < 10

    def test_empty_string(self):
        assert text_tokens("") == 0

    def test_provider_specific_tokenizer(self):
        # Should not raise even for non-OpenAI providers
        count = text_tokens("Hello, world!", model="claude-sonnet-4", provider=Provider.ANTHROPIC)
        assert count > 0

    def test_estimate_text_tokens(self):
        assert estimate_text_tokens(400) == 100
        assert estimate_text_tokens(0) == 1  # min 1


# ── Image Tokens ─────────────────────────────────────────────────────────


class TestImageTokens:
    def test_openai_high_detail_1024x1024(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="gpt-4.1", provider=Provider.OPENAI)
        assert tokens == 765  # 2×2 tiles × 170 + 85

    def test_openai_low_detail(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=4096, avg_height=4096,
            detail_level=DetailLevel.LOW,
        )
        tokens = image_input_tokens(profile, model="gpt-4.1", provider=Provider.OPENAI)
        assert tokens == 85

    def test_anthropic_image_tokens(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="claude-sonnet-4", provider=Provider.ANTHROPIC)
        assert tokens > 0
        assert tokens != 765  # Different from OpenAI

    def test_google_fixed_tokens(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="gemini-2.5-pro", provider=Provider.GOOGLE)
        assert tokens == 258

    def test_mistral_pixel_tiles(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="pixtral-large", provider=Provider.MISTRAL)
        assert tokens == 4096  # 64×64 tiles

    def test_multiple_images(self):
        profile = ImageInputProfile(
            count_per_call=3, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="gpt-4.1", provider=Provider.OPENAI)
        assert tokens == 765 * 3

    def test_fallback_for_non_vision_provider(self):
        """Non-vision providers should fallback to OpenAI formula."""
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="command-r-plus", provider=Provider.COHERE)
        assert tokens == 765  # Fallback to OpenAI

    def test_image_output_tokens(self):
        tokens = image_output_tokens(1024, 1024, "standard")
        assert tokens == 1100

    def test_image_output_tokens_hd(self):
        tokens = image_output_tokens(1024, 1024, "hd")
        assert tokens == 1600

    def test_large_image_resize(self):
        """Images >2048px should be scaled down before tiling."""
        profile = ImageInputProfile(
            count_per_call=1, avg_width=4096, avg_height=4096,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="gpt-4.1", provider=Provider.OPENAI)
        # After resize to 2048×2048, then 768 short side: 768×768
        # ceil(768/512)=2, ceil(768/512)=2 → 4 tiles × 170 + 85 = 765
        assert tokens == 765

    def test_small_image_no_resize(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=256, avg_height=256,
            detail_level=DetailLevel.HIGH,
        )
        tokens = image_input_tokens(profile, model="gpt-4.1", provider=Provider.OPENAI)
        # ceil(256/512)=1, ceil(256/512)=1 → 1 tile × 170 + 85 = 255
        assert tokens == 255


# ── Document Tokens ──────────────────────────────────────────────────────


class TestDocumentTokens:
    def test_direct_strategy(self):
        profile = DocumentInputProfile(
            count=1, avg_pages=10,
            retrieval_strategy=RetrievalStrategy.DIRECT,
        )
        tokens = document_tokens(profile)
        # 10 pages × 650 avg tokens/page
        assert tokens == 6500

    def test_rag_strategy(self):
        profile = DocumentInputProfile(
            count=1, avg_pages=10,
            retrieval_strategy=RetrievalStrategy.RAG,
        )
        tokens = document_tokens(profile)
        assert tokens == 6500  # Same as direct

    def test_file_search_strategy(self):
        profile = DocumentInputProfile(
            count=1, avg_pages=10,
            retrieval_strategy=RetrievalStrategy.FILE_SEARCH,
            top_k=5,
        )
        tokens = document_tokens(profile)
        assert tokens == 2560  # 5 chunks × 512

    def test_multiple_documents(self):
        profile = DocumentInputProfile(
            count=3, avg_pages=10,
            retrieval_strategy=RetrievalStrategy.DIRECT,
        )
        tokens = document_tokens(profile)
        assert tokens == 19500  # 3 × 10 × 650


# ── Audio Tokens ─────────────────────────────────────────────────────────


class TestAudioTokens:
    def test_openai_audio_input(self):
        profile = AudioInputProfile(avg_duration_seconds=10)
        tokens = audio_input_tokens(profile, model="gpt-4o-audio", provider=Provider.OPENAI)
        assert tokens == 430  # 10 × 43

    def test_google_audio_input(self):
        profile = AudioInputProfile(avg_duration_seconds=10)
        tokens = audio_input_tokens(profile, model="gemini-2.5-pro", provider=Provider.GOOGLE)
        assert tokens == 320  # 10 × 32

    def test_fallback_audio_rate(self):
        profile = AudioInputProfile(avg_duration_seconds=10)
        tokens = audio_input_tokens(profile, model="command-r-plus", provider=Provider.COHERE)
        assert tokens == 430  # Fallback to 43 tok/sec

    def test_audio_output_tokens(self):
        tokens = audio_output_tokens(10, model="gpt-4o-audio", provider=Provider.OPENAI)
        assert tokens == 430


# ── Reasoning Multiplier ─────────────────────────────────────────────────


class TestReasoningMultiplier:
    def test_o3(self):
        assert reasoning_token_multiplier("o3", Provider.OPENAI) == 5.0

    def test_o4_mini(self):
        assert reasoning_token_multiplier("o4-mini", Provider.OPENAI) == 3.0

    def test_gemini_pro(self):
        assert reasoning_token_multiplier("gemini-2.5-pro", Provider.GOOGLE) == 3.0

    def test_deepseek_r1(self):
        assert reasoning_token_multiplier("deepseek-r1", Provider.LOCAL) == 4.0

    def test_non_reasoning_model(self):
        assert reasoning_token_multiplier("gpt-4.1", Provider.OPENAI) == 1.0


# ── All Modality Calculator ──────────────────────────────────────────────


class TestAllModalityTokens:
    def test_text_only(self):
        result = calculate_all_modality_tokens(
            [Modality.TEXT],
            model="gpt-4.1",
            provider=Provider.OPENAI,
            system_prompt_tokens=100,
            user_input_tokens=200,
        )
        assert "text_input" in result
        assert result["text_input"] >= 300

    def test_with_image(self):
        profile = ImageInputProfile(
            count_per_call=1, avg_width=1024, avg_height=1024,
            detail_level=DetailLevel.HIGH,
        )
        result = calculate_all_modality_tokens(
            [Modality.TEXT, Modality.IMAGE_INPUT],
            model="gpt-4.1",
            provider=Provider.OPENAI,
            image_profile=profile,
        )
        assert result.get("image_input", 0) == 765
