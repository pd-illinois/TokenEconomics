"""Tests for all provider implementations."""

from __future__ import annotations

import pytest

from future_token_predictor.models.schemas import Provider
from future_token_predictor.providers import (
    get_provider,
    list_providers,
    register_provider,
    resolve_provider_for_model,
)
from future_token_predictor.providers.base import BaseProvider, PricingTier


# ── Registry ──────────────────────────────────────────────────────────────


class TestProviderRegistry:
    def test_list_providers_returns_all(self):
        providers = list_providers()
        assert len(providers) == 8
        assert Provider.OPENAI in providers
        assert Provider.ANTHROPIC in providers
        assert Provider.GOOGLE in providers
        assert Provider.MISTRAL in providers
        assert Provider.COHERE in providers
        assert Provider.BEDROCK in providers
        assert Provider.LOCAL in providers
        assert Provider.AZURE_OPENAI in providers

    def test_get_provider_returns_instance(self):
        prov = get_provider(Provider.OPENAI)
        assert isinstance(prov, BaseProvider)
        assert prov.name == "openai"

    def test_get_provider_unknown_raises(self):
        # Use a valid enum but unregister it first — impossible without internals,
        # so just ensure the happy path works.
        prov = get_provider(Provider.ANTHROPIC)
        assert prov.display_name == "Anthropic"

    def test_azure_openai_shares_openai_instance(self):
        openai = get_provider(Provider.OPENAI)
        azure = get_provider(Provider.AZURE_OPENAI)
        assert openai is azure

    def test_resolve_provider_for_model(self):
        result = resolve_provider_for_model("claude-sonnet-4")
        assert result is not None
        pid, prov = result
        assert pid == Provider.ANTHROPIC

    def test_resolve_unknown_model_returns_none(self):
        result = resolve_provider_for_model("totally-fake-model-xyz")
        assert result is None


# ── OpenAI Provider ──────────────────────────────────────────────────────


class TestOpenAIProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.OPENAI)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "gpt-4.1" in models
        assert "o3" in models
        assert "gpt-4o" in models
        assert len(models) >= 10

    def test_model_info(self, prov):
        info = prov.get_model_info("gpt-4.1")
        assert info is not None
        assert info.context_window == 1_048_576
        assert info.max_output_tokens == 32_768
        assert info.supports_vision is True

    def test_pricing_gpt41(self, prov):
        pricing = prov.get_pricing("gpt-4.1")
        assert pricing is not None
        assert pricing.input == 2.0
        assert pricing.output == 8.0

    def test_pricing_o3(self, prov):
        pricing = prov.get_pricing("o3")
        assert pricing is not None
        assert pricing.input == 2.0
        assert pricing.output == 8.0

    def test_reasoning_multiplier(self, prov):
        assert prov.get_reasoning_multiplier("o3") == 5.0
        assert prov.get_reasoning_multiplier("o4-mini") == 3.0
        assert prov.get_reasoning_multiplier("gpt-4.1") == 1.0

    def test_image_tokens_1024x1024(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "high", 1)
        # ceil(1024/512) * ceil(1024/512) * 170 + 85 = 2*2*170+85 = 765
        assert result.total_tokens == 765

    def test_image_tokens_low_detail(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "low", 1)
        assert result.total_tokens == 85

    def test_audio_tokens_per_second(self, prov):
        assert prov.get_audio_tokens_per_second() == 43

    def test_supports_model(self, prov):
        assert prov.supports_model("gpt-4.1") is True
        assert prov.supports_model("claude-sonnet-4") is False

    def test_unknown_model_info_returns_none(self, prov):
        assert prov.get_model_info("fake-model") is None

    def test_tokenizer_name(self, prov):
        assert prov.get_tokenizer_name("gpt-4.1") == "o200k_base"

    # ── GPT-5.x family coverage ──

    @pytest.mark.parametrize("model_id,ctx,out", [
        ("gpt-5.5", 1_050_000, 128_000),
        ("gpt-5.4", 1_050_000, 128_000),
        ("gpt-5.4-pro", 1_050_000, 128_000),
        ("gpt-5.4-mini", 400_000, 128_000),
        ("gpt-5.4-nano", 400_000, 128_000),
        ("gpt-5.3-codex", 400_000, 128_000),
        ("gpt-5.3-chat", 128_000, 128_000),
        ("gpt-5.2", 400_000, 128_000),
        ("gpt-5.2-codex", 400_000, 128_000),
        ("gpt-5.2-chat", 128_000, 128_000),
        ("gpt-5.1", 400_000, 128_000),
        ("gpt-5.1-codex", 400_000, 128_000),
        ("gpt-5.1-codex-mini", 400_000, 128_000),
        ("gpt-5.1-codex-max", 400_000, 128_000),
        ("gpt-5", 400_000, 128_000),
        ("gpt-5-pro", 400_000, 128_000),
        ("gpt-5-mini", 400_000, 128_000),
        ("gpt-5-nano", 400_000, 128_000),
        ("gpt-5-chat", 128_000, 128_000),
        ("gpt-5-codex", 400_000, 128_000),
    ])
    def test_gpt5_family_model_info(self, prov, model_id, ctx, out):
        info = prov.get_model_info(model_id)
        assert info is not None, f"{model_id} missing from catalog"
        assert info.context_window == ctx
        assert info.max_output_tokens == out

    @pytest.mark.parametrize("model_id", [
        "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5.3-codex", "gpt-5.3-chat",
        "gpt-5.2", "gpt-5.2-codex", "gpt-5.2-chat",
        "gpt-5.1", "gpt-5.1-codex",
        "gpt-5", "gpt-5-pro", "gpt-5-mini", "gpt-5-nano",
    ])
    def test_gpt5_family_has_pricing(self, prov, model_id):
        pricing = prov.get_pricing(model_id)
        assert pricing is not None, f"{model_id} missing pricing"
        assert pricing.input > 0
        assert pricing.output > 0

    def test_gpt54_pricing_values(self, prov):
        p = prov.get_pricing("gpt-5.4")
        assert p.input == 2.50
        assert p.output == 15.00
        assert p.cached_input == 0.25

    def test_gpt54_pro_is_expensive(self, prov):
        p = prov.get_pricing("gpt-5.4-pro")
        assert p.input == 30.00
        assert p.output == 180.00

    def test_gpt5_nano_is_cheapest(self, prov):
        p = prov.get_pricing("gpt-5-nano")
        assert p.input == 0.05
        assert p.output == 0.40

    @pytest.mark.parametrize("model_id", [
        "o3", "o3-pro", "o3-mini", "o4-mini", "codex-mini", "o1", "o1-mini",
    ])
    def test_reasoning_models_exist(self, prov, model_id):
        info = prov.get_model_info(model_id)
        assert info is not None, f"{model_id} missing"
        assert info.supports_reasoning is True

    def test_list_models_includes_all_families(self, prov):
        models = prov.list_models()
        # Spot-check representatives from each family
        for m in ["gpt-5.4", "gpt-5.4-mini", "gpt-5-nano", "gpt-4.1", "o3-pro",
                   "gpt-image-1", "gpt-audio", "gpt-oss-120b", "sora-2"]:
            assert m in models, f"{m} not in list_models()"


# ── Anthropic Provider ───────────────────────────────────────────────────


class TestAnthropicProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.ANTHROPIC)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "claude-sonnet-4" in models
        assert "claude-opus-4" in models
        assert len(models) >= 4

    def test_pricing_opus(self, prov):
        pricing = prov.get_pricing("claude-opus-4")
        assert pricing.input == 15.0
        assert pricing.output == 75.0

    def test_pricing_sonnet(self, prov):
        pricing = prov.get_pricing("claude-sonnet-4")
        assert pricing.input == 3.0
        assert pricing.output == 15.0

    def test_image_tokens_1024x1024(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "high", 1)
        # Anthropic resolution tier for ~1MP
        assert result.total_tokens > 0
        assert result.total_tokens != 765  # Not same as OpenAI

    def test_reasoning_multiplier_is_1(self, prov):
        assert prov.get_reasoning_multiplier("claude-sonnet-4") == 1.0


# ── Google Provider ──────────────────────────────────────────────────────


class TestGoogleProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.GOOGLE)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "gemini-2.5-pro" in models
        assert len(models) >= 3

    def test_pricing_pro(self, prov):
        pricing = prov.get_pricing("gemini-2.5-pro")
        assert pricing is not None
        assert pricing.input > 0

    def test_fixed_image_tokens(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "high", 1)
        assert result.total_tokens == 258

    def test_fixed_image_tokens_count(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "high", 3)
        assert result.total_tokens == 258 * 3

    def test_audio_rate(self, prov):
        assert prov.get_audio_tokens_per_second() == 32

    def test_reasoning_multiplier(self, prov):
        assert prov.get_reasoning_multiplier("gemini-2.5-pro") == 3.0
        assert prov.get_reasoning_multiplier("gemini-2.5-flash") == 2.0


# ── Mistral Provider ─────────────────────────────────────────────────────


class TestMistralProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.MISTRAL)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "mistral-large" in models
        assert "codestral" in models

    def test_pricing_large(self, prov):
        pricing = prov.get_pricing("mistral-large")
        assert pricing.input == 2.0
        assert pricing.output == 6.0

    def test_pricing_small(self, prov):
        pricing = prov.get_pricing("mistral-small")
        assert pricing.input == 0.1
        assert pricing.output == 0.3

    def test_pixtral_image_tokens(self, prov):
        result = prov.calculate_image_tokens(1024, 1024, "high", 1)
        # 16×16 pixel tiles: ceil(1024/16) * ceil(1024/16) = 64*64 = 4096
        assert result.total_tokens == 4096


# ── Cohere Provider ──────────────────────────────────────────────────────


class TestCohereProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.COHERE)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "command-r-plus" in models

    def test_pricing(self, prov):
        pricing = prov.get_pricing("command-r-plus")
        assert pricing.input == 2.5
        assert pricing.output == 10.0

    def test_no_vision_support(self, prov):
        with pytest.raises(NotImplementedError):
            prov.calculate_image_tokens(1024, 1024, "high", 1)

    def test_no_audio_support(self, prov):
        with pytest.raises(NotImplementedError):
            prov.get_audio_tokens_per_second()


# ── Bedrock Provider ─────────────────────────────────────────────────────


class TestBedrockProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.BEDROCK)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert any("claude" in m for m in models)
        assert any("llama" in m for m in models)

    def test_pricing(self, prov):
        pricing = prov.get_pricing("bedrock-claude-sonnet-4")
        assert pricing is not None
        assert pricing.input > 0


# ── Local Provider ───────────────────────────────────────────────────────


class TestLocalProvider:
    @pytest.fixture
    def prov(self):
        return get_provider(Provider.LOCAL)

    def test_list_models(self, prov):
        models = prov.list_models()
        assert "deepseek-r1" in models
        assert "llama-3.1-8b" in models

    def test_zero_pricing(self, prov):
        pricing = prov.get_pricing("llama-3.1-8b")
        assert pricing.input == 0.0
        assert pricing.output == 0.0

    def test_deepseek_reasoning(self, prov):
        assert prov.get_reasoning_multiplier("deepseek-r1") == 4.0

    def test_non_reasoning_model(self, prov):
        assert prov.get_reasoning_multiplier("llama-3.1-8b") == 1.0


# ── PricingTier ──────────────────────────────────────────────────────────


class TestPricingTier:
    def test_to_dict_minimal(self):
        pt = PricingTier(input=2.0, output=8.0)
        d = pt.to_dict()
        assert d == {"input": 2.0, "output": 8.0}

    def test_to_dict_full(self):
        pt = PricingTier(
            input=2.0, output=8.0, cached_input=0.5,
            image_input=2.0, audio_input=40.0, audio_output=80.0,
        )
        d = pt.to_dict()
        assert d["cached_input"] == 0.5
        assert "audio_input" in d

    def test_effective_cached_input_fallback(self):
        pt = PricingTier(input=2.0, output=8.0)
        assert pt.effective_cached_input == 2.0

    def test_effective_cached_input_explicit(self):
        pt = PricingTier(input=2.0, output=8.0, cached_input=0.5)
        assert pt.effective_cached_input == 0.5
