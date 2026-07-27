"""Tests for Tier 3: LLM-assisted token estimation."""

from __future__ import annotations

import json

import pytest

from future_token_predictor.history.tier3_estimator import (
    MockLLMClient,
    OpenAICompatibleClient,
    Tier3Estimate,
    _parse_estimate,
    apply_tier3,
    _MIN_RATIO,
    _MAX_RATIO,
    _MIN_CONFIDENCE,
)
from future_token_predictor.models.schemas import (
    ModalityBreakdown,
    UseCaseProfile,
    Provider,
    Complexity,
)


# ── Parse Estimate Tests ──


class TestParseEstimate:
    def test_valid_json(self):
        content = json.dumps({
            "estimated_output_tokens": 500,
            "estimated_input_tokens": 800,
            "estimated_reasoning_tokens": 0,
            "estimated_steps": 2,
            "confidence": 0.85,
            "reasoning": "Simple text task",
        })
        result = _parse_estimate(content, "gpt-4.1-nano")
        assert result is not None
        assert result.estimated_output_tokens == 500
        assert result.estimated_input_tokens == 800
        assert result.confidence == 0.85
        assert result.estimated_steps == 2
        assert result.model_used == "gpt-4.1-nano"

    def test_json_with_markdown_fences(self):
        content = '```json\n{"estimated_output_tokens": 300, "confidence": 0.7}\n```'
        result = _parse_estimate(content, "test")
        assert result is not None
        assert result.estimated_output_tokens == 300

    def test_invalid_json(self):
        result = _parse_estimate("not json at all", "test")
        assert result is None

    def test_missing_output_tokens(self):
        content = json.dumps({"confidence": 0.8})
        result = _parse_estimate(content, "test")
        assert result is None  # No estimated_output_tokens

    def test_negative_output_tokens(self):
        content = json.dumps({
            "estimated_output_tokens": -100,
            "confidence": 0.5,
        })
        result = _parse_estimate(content, "test")
        assert result is None

    def test_confidence_clamped(self):
        content = json.dumps({
            "estimated_output_tokens": 500,
            "confidence": 1.5,  # Over 1.0
        })
        result = _parse_estimate(content, "test")
        assert result is not None
        assert result.confidence == 1.0

    def test_confidence_floor(self):
        content = json.dumps({
            "estimated_output_tokens": 500,
            "confidence": -0.5,  # Negative
        })
        result = _parse_estimate(content, "test")
        assert result is not None
        assert result.confidence == 0.0

    def test_non_dict_json(self):
        result = _parse_estimate("[1, 2, 3]", "test")
        assert result is None

    def test_float_output_tokens_accepted(self):
        content = json.dumps({
            "estimated_output_tokens": 500.7,
            "confidence": 0.8,
        })
        result = _parse_estimate(content, "test")
        assert result is not None
        assert result.estimated_output_tokens == 500


# ── Apply Tier 3 Tests ──


class TestApplyTier3:
    def test_basic_blend(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=600,
            estimated_input_tokens=1100,
            confidence=0.8,
        )
        result = apply_tier3(tier1, estimate)
        assert result is not None
        # Blend: (1-0.8)*500 + 0.8*600 = 100 + 480 = 580
        assert abs(result.text_output - 580.0) < 0.01
        # Blend: (1-0.8)*1000 + 0.8*1100 = 200 + 880 = 1080
        assert abs(result.text_input - 1080.0) < 0.01

    def test_low_confidence_rejected(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=600,
            confidence=0.1,  # Below _MIN_CONFIDENCE
        )
        result = apply_tier3(tier1, estimate)
        assert result is None

    def test_output_too_high_rejected(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=15000,  # 30× tier1 → exceeds _MAX_RATIO (25)
            confidence=0.8,
        )
        result = apply_tier3(tier1, estimate)
        assert result is None

    def test_output_too_low_rejected(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=5,  # 1% of tier1 → below _MIN_RATIO (0.1)
            confidence=0.8,
        )
        result = apply_tier3(tier1, estimate)
        assert result is None

    def test_input_too_divergent_rejected(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=500,
            estimated_input_tokens=50,  # 5% of tier1 → below _MIN_RATIO
            confidence=0.8,
        )
        result = apply_tier3(tier1, estimate)
        assert result is None

    def test_image_tokens_preserved(self):
        tier1 = ModalityBreakdown(
            text_input=1000, text_output=500, image_input=765.0
        )
        estimate = Tier3Estimate(
            estimated_output_tokens=600,
            confidence=0.8,
        )
        result = apply_tier3(tier1, estimate)
        assert result is not None
        assert result.image_input == 765.0  # Untouched

    def test_document_tokens_preserved(self):
        tier1 = ModalityBreakdown(
            text_input=1000, text_output=500, document_input=2560.0
        )
        estimate = Tier3Estimate(
            estimated_output_tokens=600,
            confidence=0.5,
        )
        result = apply_tier3(tier1, estimate)
        assert result is not None
        assert result.document_input == 2560.0

    def test_zero_tier1_output_still_works(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=0)
        estimate = Tier3Estimate(
            estimated_output_tokens=500,
            confidence=0.8,
        )
        # No ratio check when tier1 is 0
        result = apply_tier3(tier1, estimate)
        assert result is not None
        assert result.text_output == 0.8 * 500  # Full blend weight

    def test_zero_estimate_uses_tier1(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=0,
            confidence=0.8,
        )
        # When estimate is 0, keep tier1 value
        result = apply_tier3(tier1, estimate)
        assert result is not None
        assert result.text_output == 500.0

    def test_reasoning_tokens_blended(self):
        tier1 = ModalityBreakdown(
            text_input=1000, text_output=500, reasoning=2000.0
        )
        estimate = Tier3Estimate(
            estimated_output_tokens=600,
            estimated_reasoning_tokens=2500,
            confidence=0.6,
        )
        result = apply_tier3(tier1, estimate)
        assert result is not None
        # (1-0.6)*2000 + 0.6*2500 = 800 + 1500 = 2300
        assert abs(result.reasoning - 2300.0) < 0.01

    def test_boundary_ratio_accepted(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        # Exactly at _MIN_RATIO (0.2) → 100 tokens
        estimate = Tier3Estimate(
            estimated_output_tokens=100,
            confidence=0.5,
        )
        result = apply_tier3(tier1, estimate)
        # 100/500 = 0.2 → exactly at boundary, should NOT be rejected (≥ check)
        assert result is not None

    def test_full_confidence_uses_llm_estimate(self):
        tier1 = ModalityBreakdown(text_input=1000, text_output=500)
        estimate = Tier3Estimate(
            estimated_output_tokens=700,
            estimated_input_tokens=1200,
            confidence=1.0,
        )
        result = apply_tier3(tier1, estimate)
        assert result is not None
        assert abs(result.text_output - 700.0) < 0.01
        assert abs(result.text_input - 1200.0) < 0.01


# ── Mock Client Tests ──


class TestMockLLMClient:
    def test_default_estimate(self):
        client = MockLLMClient()
        profile = UseCaseProfile(model="gpt-4.1", provider=Provider.OPENAI)
        result = client.estimate_tokens("test prompt", profile)
        assert result is not None
        assert result.estimated_output_tokens == 500
        assert result.model_used == "mock"

    def test_custom_estimates(self):
        custom = Tier3Estimate(
            estimated_output_tokens=1000,
            confidence=0.9,
            model_used="custom-mock",
        )
        client = MockLLMClient(estimates={"gpt-4.1": custom})
        profile = UseCaseProfile(model="gpt-4.1")
        result = client.estimate_tokens("test", profile)
        assert result.estimated_output_tokens == 1000

    def test_fallback_to_default(self):
        client = MockLLMClient(estimates={"gpt-4.1": Tier3Estimate(estimated_output_tokens=999)})
        profile = UseCaseProfile(model="claude-sonnet-4")
        result = client.estimate_tokens("test", profile)
        assert result.estimated_output_tokens == 500  # Default


# ── OpenAI Client Tests ──


class TestOpenAICompatibleClient:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = OpenAICompatibleClient(api_key="")
        profile = UseCaseProfile(model="gpt-4.1")
        result = client.estimate_tokens("test", profile)
        assert result is None


# ── Predictor Integration Tests ──


class TestPredictorTier3Integration:
    def test_tier3_with_mock_client(self, tmp_path):
        from future_token_predictor import predict

        mock = MockLLMClient()
        result = predict(
            description="Simple GPT-4.1 text chatbot",
            enable_tier2=False,
            enable_tier3=True,
            tier3_client=mock,
            db_path=str(tmp_path / "test.db"),
        )
        assert result.prediction_method == "tier3_llm_assisted"

    def test_tier3_disabled_by_default(self, tmp_path):
        from future_token_predictor import predict

        result = predict(
            description="Simple GPT-4.1 text chatbot",
            enable_tier2=False,
            db_path=str(tmp_path / "test.db"),
        )
        assert result.prediction_method == "tier1_heuristic"

    def test_tier3_with_bad_estimate_falls_back(self, tmp_path):
        from future_token_predictor import predict

        # Mock that returns wildly divergent estimates
        wild = Tier3Estimate(
            estimated_output_tokens=100000,  # Way too high
            confidence=0.9,
            model_used="bad-mock",
        )
        client = MockLLMClient(estimates={"gpt-4.1": wild})

        result = predict(
            description="Simple GPT-4.1 text chatbot",
            enable_tier2=False,
            enable_tier3=True,
            tier3_client=client,
            db_path=str(tmp_path / "test.db"),
        )
        # Should fall back to tier1 because estimate fails validation
        assert result.prediction_method == "tier1_heuristic"

    def test_tier3_after_tier2(self, tmp_path):
        """When both tiers are enabled and have data, tier3 should win."""
        from future_token_predictor import predict
        from future_token_predictor.history.database import HistoryDatabase
        # Seed tier2 data
        from tests.test_tier2 import _seed_calibration_data

        db_path = str(tmp_path / "test.db")
        db = HistoryDatabase(db_path)
        _seed_calibration_data(
            db, model="gpt-4.1", archetype="SingleCall_TextOnly",
            n=25, slope=1.2, intercept=50.0, noise_std=5.0,
        )
        db.close()

        mock = MockLLMClient()
        result = predict(
            description="Simple GPT-4.1 text chatbot",
            enable_tier2=True,
            enable_tier3=True,
            tier3_client=mock,
            db_path=db_path,
        )
        # Tier 3 runs after Tier 2, so it should be the final method
        assert result.prediction_method == "tier3_llm_assisted"

    def test_tier2_only_when_tier3_fails(self, tmp_path):
        from future_token_predictor import predict
        from future_token_predictor.history.database import HistoryDatabase
        from tests.test_tier2 import _seed_calibration_data

        db_path = str(tmp_path / "test.db")
        db = HistoryDatabase(db_path)
        _seed_calibration_data(
            db, model="gpt-4.1", archetype="SingleCall_TextOnly",
            n=25, slope=1.2, intercept=50.0, noise_std=5.0,
        )
        db.close()

        # Low confidence → rejected
        bad = Tier3Estimate(
            estimated_output_tokens=500,
            confidence=0.1,
            model_used="bad-mock",
        )
        client = MockLLMClient(estimates={"gpt-4.1": bad})

        result = predict(
            description="Simple GPT-4.1 text chatbot",
            enable_tier2=True,
            enable_tier3=True,
            tier3_client=client,
            db_path=db_path,
        )
        assert result.prediction_method == "tier2_calibrated"
