"""Tests for the LLM-based classifier module."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import respx

from future_token_predictor.llm_classifier import (
    _parse_classification,
    _resolve_credentials,
    classify_with_llm,
)


# ── Parse tests ──────────────────────────────────────────────────────


class TestParseClassification:
    def test_valid_json(self):
        raw = json.dumps(
            {
                "model": "gpt-5.4-mini",
                "provider": "openai",
                "agent_type": "prompt",
                "modalities": ["text"],
                "complexity": "low",
                "reasoning": False,
            }
        )
        result = _parse_classification(raw)
        assert result is not None
        assert result.model == "gpt-5.4-mini"
        assert result.provider == "openai"
        assert result.agent_type == "prompt"

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"model":"gpt-5","provider":"openai"}\n```'
        result = _parse_classification(raw)
        assert result is not None
        assert result.model == "gpt-5"

    def test_invalid_json_returns_none(self):
        assert _parse_classification("not json at all") is None

    def test_missing_model_returns_none(self):
        raw = json.dumps({"provider": "openai"})
        assert _parse_classification(raw) is None

    def test_missing_provider_returns_none(self):
        raw = json.dumps({"model": "gpt-5"})
        assert _parse_classification(raw) is None

    def test_uppercase_normalized(self):
        raw = json.dumps({"model": "GPT-5.4-Pro", "provider": "OpenAI"})
        result = _parse_classification(raw)
        assert result.model == "gpt-5.4-pro"
        assert result.provider == "openai"

    def test_modalities_preserved(self):
        raw = json.dumps(
            {
                "model": "gpt-5",
                "provider": "openai",
                "modalities": ["text", "image_input"],
            }
        )
        result = _parse_classification(raw)
        assert result.modalities == ["text", "image_input"]

    def test_defaults_applied(self):
        raw = json.dumps({"model": "gpt-5", "provider": "openai"})
        result = _parse_classification(raw)
        assert result.agent_type == "prompt"
        assert result.complexity == "medium"
        assert result.reasoning is False


# ── Credential resolution tests ──────────────────────────────────────


class TestResolveCredentials:
    def test_explicit_args(self):
        key, url, model = _resolve_credentials("mykey", "http://localhost", "gpt-5")
        assert key == "mykey"
        assert url == "http://localhost"
        assert model == "gpt-5"

    def test_env_classifier_vars(self):
        env = {
            "CLASSIFIER_API_KEY": "ckey",
            "CLASSIFIER_ENDPOINT": "http://cls/",
            "CLASSIFIER_MODEL": "gpt-4.1-nano",
        }
        with patch.dict("os.environ", env, clear=False):
            key, url, model = _resolve_credentials(None, None, None)
        assert key == "ckey"
        assert url == "http://cls"
        assert model == "gpt-4.1-nano"

    def test_env_openai_fallback(self):
        env = {"OPENAI_API_KEY": "oai-key"}
        with patch.dict("os.environ", env, clear=False):
            key, url, model = _resolve_credentials(None, None, None)
        assert key == "oai-key"
        assert "openai.com" in url
        assert model == "gpt-4.1-nano"

    def test_no_keys_returns_empty(self):
        env = {
            "CLASSIFIER_API_KEY": "",
            "AZURE_OPENAI_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict("os.environ", env, clear=True):
            key, url, model = _resolve_credentials(None, None, None)
        assert key == ""


# ── End-to-end classify_with_llm with mocked HTTP ───────────────────


class TestClassifyWithLLM:
    @respx.mock
    def test_successful_classification(self):
        response_body = json.dumps(
            {
                "model": "gpt-5.4",
                "provider": "openai",
                "agent_type": "hosted",
                "modalities": ["text", "image_input"],
                "complexity": "high",
                "reasoning": False,
            }
        )
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": response_body}}],
                },
            )
        )
        result = classify_with_llm(
            "Build an autonomous agent with GPT-5.4",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-nano",
        )
        assert result is not None
        assert result.model == "gpt-5.4"
        assert result.provider == "openai"
        assert result.agent_type == "hosted"

    @respx.mock
    def test_api_failure_returns_none(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(500)
        )
        result = classify_with_llm(
            "Test description",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-nano",
        )
        assert result is None

    def test_no_api_key_returns_none(self):
        env = {
            "CLASSIFIER_API_KEY": "",
            "AZURE_OPENAI_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict("os.environ", env, clear=True):
            result = classify_with_llm("Test description")
        assert result is None

    @respx.mock
    def test_invalid_json_response_returns_none(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "I don't understand"}}],
                },
            )
        )
        result = classify_with_llm(
            "Test",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-nano",
        )
        assert result is None


# ── Integration with classifier.classify (LLM path) ─────────────────


class TestClassifierLLMIntegration:
    """Test that classifier.classify uses LLM results when available."""

    @respx.mock
    def test_llm_result_used_when_available(self):
        from future_token_predictor.classifier import classify
        from future_token_predictor.models.schemas import Provider

        response_body = json.dumps(
            {
                "model": "gpt-5.4-mini",
                "provider": "azure_openai",
                "agent_type": "workflow",
                "modalities": ["text", "document"],
                "complexity": "high",
                "reasoning": False,
            }
        )
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": response_body}}],
                },
            )
        )
        env = {"OPENAI_API_KEY": "test-key"}
        with patch.dict("os.environ", env, clear=False):
            profile = classify(
                "Build a document processing pipeline with GPT-5.4 mini on Azure"
            )

        assert profile.model == "gpt-5.4-mini"
        assert profile.provider == Provider.AZURE_OPENAI

    def test_regex_fallback_when_no_key(self):
        """Without API key, should fall back to regex-based detection."""
        from future_token_predictor.classifier import classify
        from future_token_predictor.models.schemas import Provider

        env = {
            "CLASSIFIER_API_KEY": "",
            "AZURE_OPENAI_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict("os.environ", env, clear=True):
            profile = classify("Use GPT-4.1 for a chatbot")

        assert profile.model == "gpt-4.1"
        assert profile.provider == Provider.OPENAI

    def test_llm_modalities_cannot_erase_deterministic_evidence(self):
        from future_token_predictor.classifier import classify
        from future_token_predictor.llm_classifier import LLMClassification
        from future_token_predictor.models.schemas import Modality

        llm_result = LLMClassification(
            model="gpt-4.1",
            provider="openai",
            modalities=["text"],
        )
        with patch(
            "future_token_predictor.classifier._try_llm_classification",
            return_value=llm_result,
        ):
            profile = classify("Analyze 3 scanned contracts and recorded audio")

        assert Modality.DOCUMENT in profile.modalities
        assert Modality.IMAGE_INPUT in profile.modalities
        assert Modality.AUDIO_INPUT in profile.modalities

    def test_unknown_llm_modalities_are_ignored_without_losing_fallback(self):
        from future_token_predictor.classifier import classify
        from future_token_predictor.llm_classifier import LLMClassification
        from future_token_predictor.models.schemas import Modality

        llm_result = LLMClassification(
            model="gpt-4.1",
            provider="openai",
            modalities=["text", "invented_modality"],
        )
        with patch(
            "future_token_predictor.classifier._try_llm_classification",
            return_value=llm_result,
        ):
            profile = classify("Search uploaded PDF documents")

        assert profile.modalities == [Modality.TEXT, Modality.DOCUMENT]


# ── Tolerant JSON extraction ─────────────────────────────────────────


class TestParseRobustness:
    def test_json_embedded_in_prose(self):
        raw = 'Here is the result: {"model":"gpt-5","provider":"openai"}'
        result = _parse_classification(raw)
        assert result is not None
        assert result.model == "gpt-5"
        assert result.provider == "openai"

    def test_prose_before_and_after_json(self):
        raw = 'Sure!\n{"model":"claude-sonnet-4","provider":"anthropic"}\nHope that helps.'
        result = _parse_classification(raw)
        assert result is not None
        assert result.model == "claude-sonnet-4"
        assert result.provider == "anthropic"

    def test_no_json_object_returns_none(self):
        assert _parse_classification("there is no object here") is None


# ── Capability cost-lever parsing ────────────────────────────────────


class TestCapabilityFlags:
    def test_flags_parsed(self):
        raw = json.dumps(
            {
                "model": "gpt-5",
                "provider": "openai",
                "uses_prompt_caching": True,
                "uses_batch_api": True,
                "uses_streaming": True,
                "uses_retrieval": True,
            }
        )
        result = _parse_classification(raw)
        assert result is not None
        assert result.uses_prompt_caching is True
        assert result.uses_batch_api is True
        assert result.uses_streaming is True
        assert result.uses_retrieval is True

    def test_flags_default_false(self):
        raw = json.dumps({"model": "gpt-5", "provider": "openai"})
        result = _parse_classification(raw)
        assert result.uses_prompt_caching is False
        assert result.uses_batch_api is False
        assert result.uses_streaming is False
        assert result.uses_retrieval is False
