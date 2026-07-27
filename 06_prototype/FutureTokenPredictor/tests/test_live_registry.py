"""Tests for live model registry — provider API fetchers and disk cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from future_token_predictor.providers.live_registry import (
    CACHE_TTL_SECONDS,
    LiveModelEntry,
    LivePricingEntry,
    fetch_openai_models,
    fetch_anthropic_models,
    fetch_google_models,
    fetch_mistral_models,
    fetch_azure_pricing,
    fetch_live_models,
    invalidate_cache,
    _cache_path,
    _pricing_cache_path,
    _is_cache_fresh,
    _write_cache,
)
from future_token_predictor.providers.base import ModelInfo


# ─── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Redirect cache dir to tmp_path for test isolation."""
    monkeypatch.setattr(
        "future_token_predictor.providers.live_registry.CACHE_DIR", tmp_path
    )
    # Clear any env keys to prevent real API calls
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)


# ─── Cache tests ───────────────────────────────────────────────────

class TestDiskCache:
    def test_fresh_cache(self, tmp_path):
        path = tmp_path / "test.json"
        _write_cache(path, [{"model_id": "x", "provider": "openai"}])
        assert _is_cache_fresh(path)

    def test_stale_cache(self, tmp_path):
        path = tmp_path / "test.json"
        _write_cache(path, [{"model_id": "x", "provider": "openai"}])
        # Backdate the file
        old_time = time.time() - CACHE_TTL_SECONDS - 100
        import os
        os.utime(path, (old_time, old_time))
        assert not _is_cache_fresh(path)

    def test_missing_cache(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        assert not _is_cache_fresh(path)

    def test_invalidate_single(self, tmp_path):
        path = tmp_path / "openai_models.json"
        _write_cache(path, [])
        assert path.exists()
        invalidate_cache("openai")
        assert not path.exists()

    def test_invalidate_all(self, tmp_path):
        _write_cache(tmp_path / "openai_models.json", [])
        _write_cache(tmp_path / "anthropic_models.json", [])
        invalidate_cache()
        assert not list(tmp_path.glob("*.json"))


# ─── No-API-key tests ─────────────────────────────────────────────

class TestNoApiKey:
    """When no API key is set, fetchers return [] without making requests."""

    def test_openai_no_key(self):
        assert fetch_openai_models() == []

    def test_anthropic_no_key(self):
        assert fetch_anthropic_models() == []

    def test_google_no_key(self):
        assert fetch_google_models() == []

    def test_mistral_no_key(self):
        assert fetch_mistral_models() == []


# ─── OpenAI fetcher tests ─────────────────────────────────────────

class TestOpenAIFetcher:
    @respx.mock
    def test_fetches_relevant_models(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {"id": "gpt-4.1", "owned_by": "openai"},
                    {"id": "gpt-5.4", "owned_by": "openai"},
                    {"id": "o3-mega", "owned_by": "openai"},
                    {"id": "text-embedding-3-small", "owned_by": "openai"},  # filtered out
                    {"id": "whisper-1", "owned_by": "openai"},  # filtered out
                ]
            })
        )
        models = fetch_openai_models()
        ids = [m.model_id for m in models]
        assert "gpt-4.1" in ids
        assert "gpt-5.4" in ids
        assert "o3-mega" in ids
        assert "text-embedding-3-small" not in ids
        assert "whisper-1" not in ids

    @respx.mock
    def test_uses_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        # Pre-fill cache
        _write_cache(
            tmp_path / "openai_models.json",
            [{"model_id": "gpt-cached", "provider": "openai"}],
        )
        # Should NOT make HTTP request
        models = fetch_openai_models()
        assert len(models) == 1
        assert models[0].model_id == "gpt-cached"

    @respx.mock
    def test_handles_http_error(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(500)
        )
        assert fetch_openai_models() == []


# ─── Anthropic fetcher tests ──────────────────────────────────────

class TestAnthropicFetcher:
    @respx.mock
    def test_parses_capabilities(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {
                        "id": "claude-opus-4-6",
                        "display_name": "Claude Opus 4.6",
                        "max_input_tokens": 200000,
                        "max_tokens": 32000,
                        "capabilities": {
                            "image_input": {"supported": True},
                            "thinking": {"supported": True},
                        },
                    },
                    {
                        "id": "claude-haiku-4",
                        "display_name": "Claude Haiku 4",
                        "max_input_tokens": 200000,
                        "max_tokens": 8192,
                        "capabilities": {
                            "image_input": {"supported": True},
                            "thinking": {"supported": False},
                        },
                    },
                ]
            })
        )
        models = fetch_anthropic_models()
        assert len(models) == 2
        opus = next(m for m in models if m.model_id == "claude-opus-4-6")
        assert opus.context_window == 200000
        assert opus.max_output_tokens == 32000
        assert opus.supports_vision is True
        assert opus.supports_reasoning is True


# ─── Google fetcher tests ─────────────────────────────────────────

class TestGoogleFetcher:
    @respx.mock
    def test_parses_gemini_models(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(200, json={
                "models": [
                    {
                        "name": "models/gemini-2.5-pro-001",
                        "baseModelId": "gemini-2.5-pro",
                        "displayName": "Gemini 2.5 Pro",
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536,
                        "thinking": True,
                    },
                    {
                        "name": "models/gemini-3.0-flash",
                        "baseModelId": "gemini-3.0-flash",
                        "displayName": "Gemini 3.0 Flash",
                        "inputTokenLimit": 2097152,
                        "outputTokenLimit": 131072,
                        "thinking": True,
                    },
                    {
                        "name": "models/text-embedding-004",
                        "baseModelId": "text-embedding-004",
                        "displayName": "Text Embedding",
                        "inputTokenLimit": 2048,
                        "outputTokenLimit": 0,
                    },
                ],
            })
        )
        models = fetch_google_models()
        ids = [m.model_id for m in models]
        assert "gemini-2.5-pro" in ids
        assert "gemini-3.0-flash" in ids
        assert "text-embedding-004" not in ids  # not gemini

    @respx.mock
    def test_deduplicates_variants(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(200, json={
                "models": [
                    {"name": "models/gemini-2.5-pro-001", "baseModelId": "gemini-2.5-pro", "inputTokenLimit": 1048576, "outputTokenLimit": 65536},
                    {"name": "models/gemini-2.5-pro-002", "baseModelId": "gemini-2.5-pro", "inputTokenLimit": 1048576, "outputTokenLimit": 65536},
                ],
            })
        )
        models = fetch_google_models()
        assert len(models) == 1


# ─── Mistral fetcher tests ────────────────────────────────────────

class TestMistralFetcher:
    @respx.mock
    def test_fetches_models(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        respx.get("https://api.mistral.ai/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {
                        "id": "mistral-large-latest",
                        "name": "Mistral Large",
                        "capabilities": {"vision": True},
                        "max_context_length": 128000,
                        "owned_by": "mistral",
                    },
                ]
            })
        )
        models = fetch_mistral_models()
        assert len(models) == 1
        assert models[0].model_id == "mistral-large-latest"
        assert models[0].supports_vision is True


# ─── Azure pricing fetcher tests ──────────────────────────────────

class TestAzurePricing:
    @respx.mock
    def test_parses_pricing(self):
        respx.get("https://prices.azure.com/api/retail/prices").mock(
            return_value=httpx.Response(200, json={
                "Items": [
                    {
                        "meterName": "GPT-4o Input Tokens",
                        "productName": "Azure OpenAI GPT-4o",
                        "unitPrice": 0.0025,
                        "unitOfMeasure": "1K Tokens",
                    },
                    {
                        "meterName": "GPT-4o Output Tokens",
                        "productName": "Azure OpenAI GPT-4o",
                        "unitPrice": 0.01,
                        "unitOfMeasure": "1K Tokens",
                    },
                    {
                        "meterName": "GPT-4o Cached Input Tokens",
                        "productName": "Azure OpenAI GPT-4o",
                        "unitPrice": 0.00125,
                        "unitOfMeasure": "1K Tokens",
                    },
                ],
                "NextPageLink": None,
            })
        )
        pricing = fetch_azure_pricing()
        assert len(pricing) == 1
        p = pricing[0]
        assert p.model_id == "gpt-4o"
        assert p.input_per_1m == 2.50
        assert p.output_per_1m == 10.00
        assert p.cached_input_per_1m == 1.25

    @respx.mock
    def test_handles_failure(self):
        respx.get("https://prices.azure.com/api/retail/prices").mock(
            return_value=httpx.Response(503)
        )
        assert fetch_azure_pricing() == []


# ─── LiveModelEntry conversion tests ──────────────────────────────

class TestLiveModelEntry:
    def test_to_model_info_with_data(self):
        entry = LiveModelEntry(
            model_id="gpt-6",
            provider="openai",
            context_window=2_000_000,
            max_output_tokens=100_000,
            supports_vision=True,
            supports_reasoning=True,
        )
        info = entry.to_model_info()
        assert info.name == "gpt-6"
        assert info.context_window == 2_000_000
        assert info.max_output_tokens == 100_000
        assert info.supports_vision is True

    def test_to_model_info_with_fallback(self):
        entry = LiveModelEntry(
            model_id="gpt-4.1",
            provider="openai",
            # No context_window — should use fallback
        )
        fallback = ModelInfo(
            name="gpt-4.1", provider="openai",
            context_window=1_048_576, max_output_tokens=32_768,
            supports_vision=True, supports_caching=True,
            tokenizer="o200k_base",
        )
        info = entry.to_model_info(fallback=fallback)
        assert info.context_window == 1_048_576  # from fallback
        assert info.tokenizer == "o200k_base"

    def test_to_model_info_defaults(self):
        entry = LiveModelEntry(model_id="unknown-model", provider="openai")
        info = entry.to_model_info()
        assert info.context_window == 128_000  # default
        assert info.max_output_tokens == 8_192  # default


class TestLivePricingEntry:
    def test_to_pricing_tier(self):
        entry = LivePricingEntry(
            model_id="gpt-4.1",
            input_per_1m=2.00,
            output_per_1m=8.00,
            cached_input_per_1m=0.50,
        )
        tier = entry.to_pricing_tier()
        assert tier.input == 2.00
        assert tier.output == 8.00
        assert tier.cached_input == 0.50


# ─── Unified interface tests ──────────────────────────────────────

class TestFetchLiveModels:
    def test_unknown_provider_returns_empty(self):
        assert fetch_live_models("nonexistent") == []

    @respx.mock
    def test_dispatches_to_correct_fetcher(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [{"id": "gpt-4.1", "owned_by": "openai"}]
            })
        )
        models = fetch_live_models("openai")
        assert len(models) == 1


# ─── Provider integration tests ───────────────────────────────────

class TestProviderLiveIntegration:
    """Test that providers fall back to live registry for unknown models."""

    @respx.mock
    def test_openai_discovers_new_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {"id": "gpt-7", "owned_by": "openai"},
                ]
            })
        )
        from future_token_predictor.providers.openai_provider import OpenAIProvider
        prov = OpenAIProvider()
        # gpt-7 is not in static catalog but discovered live
        info = prov.get_model_info("gpt-7")
        assert info is not None
        assert info.name == "gpt-7"

    @respx.mock
    def test_anthropic_discovers_new_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [{
                    "id": "claude-opus-5",
                    "display_name": "Claude Opus 5",
                    "max_input_tokens": 500000,
                    "max_tokens": 64000,
                    "capabilities": {
                        "image_input": {"supported": True},
                        "thinking": {"supported": True},
                    },
                }]
            })
        )
        from future_token_predictor.providers.anthropic_provider import AnthropicProvider
        prov = AnthropicProvider()
        info = prov.get_model_info("claude-opus-5")
        assert info is not None
        assert info.context_window == 500000

    def test_static_model_still_works(self):
        """Static catalog should still work when no API keys are set."""
        from future_token_predictor.providers.openai_provider import OpenAIProvider
        prov = OpenAIProvider()
        info = prov.get_model_info("gpt-4.1")
        assert info is not None
        assert info.context_window == 1_048_576

    @respx.mock
    def test_list_models_merges_live_and_static(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {"id": "gpt-4.1", "owned_by": "openai"},  # duplicate of static
                    {"id": "gpt-7", "owned_by": "openai"},  # new
                ]
            })
        )
        from future_token_predictor.providers.openai_provider import OpenAIProvider
        prov = OpenAIProvider()
        models = prov.list_models()
        assert "gpt-4.1" in models  # static
        assert "gpt-7" in models  # live
        # No duplicates
        assert models.count("gpt-4.1") == 1
