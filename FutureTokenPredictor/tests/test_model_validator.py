"""Tests for model_validator — dot/dash normalization and live catalog integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from future_token_predictor.model_validator import (
    ModelValidationResult,
    ValidationStatus,
    _check_azure_ai_catalog,
    _find_family_fallback,
    _normalize_model_id,
    _version_variants,
    validate_model,
)


# ─── _version_variants ────────────────────────────────────────────


class TestVersionVariants:
    """Dot↔dash variant generation."""

    def test_dot_to_dash(self):
        variants = _version_variants("claude-opus-4.6")
        assert "claude-opus-4-6" in variants
        assert "claude-opus-4.6" in variants

    def test_dash_to_dot(self):
        variants = _version_variants("claude-opus-4-6")
        assert "claude-opus-4.6" in variants
        assert "claude-opus-4-6" in variants

    def test_no_version_number_unchanged(self):
        variants = _version_variants("gpt-4o-mini")
        # "4o" is not digit-digit, so should not produce a dot variant
        assert "gpt-4o-mini" in variants

    def test_multi_version_segments(self):
        variants = _version_variants("llama-3.2-11b")
        assert "llama-3-2-11b" in variants  # dot→dash on 3.2
        assert "llama-3.2-11b" in variants  # original kept

    def test_already_canonical(self):
        variants = _version_variants("gpt-5.4")
        assert "gpt-5.4" in variants
        assert "gpt-5-4" in variants


# ─── _normalize_model_id ──────────────────────────────────────────


class TestNormalizeModelId:
    def test_lowercase(self):
        assert _normalize_model_id("GPT-4O") == "gpt-4o"

    def test_strip_prefix(self):
        assert _normalize_model_id("openai/gpt-4o") == "gpt-4o"
        assert _normalize_model_id("anthropic/claude-opus-4") == "claude-opus-4"

    def test_no_prefix(self):
        assert _normalize_model_id("claude-opus-4.6") == "claude-opus-4.6"


# ─── _check_azure_ai_catalog (hardcoded fallback) ────────────────


class TestCheckAzureAiCatalog:
    """Tests with the live catalog API mocked out (tests hardcoded fallback)."""

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_dot_variant_matches_dash_catalog(self, _mock):
        """claude-opus-4.6 (dots) should match claude-opus-4-6 (dashes) in hardcoded set."""
        # The hardcoded set has "claude-opus-4-6" (dashes).
        # Calling with dots should still match via _version_variants.
        assert _check_azure_ai_catalog("claude-opus-4.6") is True

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_exact_match_dashes(self, _mock):
        assert _check_azure_ai_catalog("claude-opus-4-6") is True

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_gpt_dot_notation(self, _mock):
        assert _check_azure_ai_catalog("gpt-5.4") is True

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_gpt4o_mini_not_corrupted(self, _mock):
        """gpt-4o-mini must not turn into gpt-4o.mini or similar."""
        assert _check_azure_ai_catalog("gpt-4o-mini") is True

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_nonexistent_model(self, _mock):
        assert _check_azure_ai_catalog("totally-fake-model-999") is False


# ─── Live catalog integration ────────────────────────────────────


class TestCheckAzureAiCatalogLive:
    """Tests that the live catalog path is tried first."""

    @patch("future_token_predictor.providers.foundry_catalog.check_model_exists", return_value=True)
    def test_live_hit_returns_true(self, mock_check):
        assert _check_azure_ai_catalog("some-new-model") is True
        mock_check.assert_called()

    @patch("future_token_predictor.providers.foundry_catalog.check_model_exists", return_value=False)
    def test_live_miss_falls_through_to_hardcoded(self, mock_check):
        """When live says no, hardcoded set is still checked."""
        # "gpt-4o" is in the hardcoded set
        assert _check_azure_ai_catalog("gpt-4o") is True

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("network error"),
    )
    def test_live_error_falls_through_gracefully(self, _mock):
        assert _check_azure_ai_catalog("gpt-4o") is True


# ─── _find_family_fallback with dot/dash ─────────────────────────


class TestFamilyFallbackDotDash:
    def test_claude_dot_variant_finds_family(self):
        """claude-opus-4.6 should match the 'claude-opus' family prefix."""
        result = _find_family_fallback("claude-opus-4.6", "anthropic")
        assert result is not None
        resolved, provider = result
        assert provider == "anthropic"
        # Should resolve to claude-opus-4 (first in family list that exists)
        assert "claude" in resolved

    def test_gpt_dash_variant_finds_family(self):
        result = _find_family_fallback("gpt-5-4", "openai")
        assert result is not None


# ─── validate_model end-to-end (mocked live catalog) ─────────────


class TestValidateModelEndToEnd:
    """Integration tests for the full cascade."""

    @patch("future_token_predictor.providers.foundry_catalog.check_model_exists", return_value=True)
    def test_dot_model_not_substituted(self, _mock):
        """The key bug: claude-opus-4.6 should NOT be substituted to claude-opus-4."""
        result = validate_model("claude-opus-4.6")
        assert result.status != ValidationStatus.SUBSTITUTED
        assert result.is_valid

    @patch("future_token_predictor.providers.foundry_catalog.check_model_exists", return_value=False)
    def test_hardcoded_fallback_dot_model(self, _mock):
        """Even without live API, dot variant should match hardcoded dashes."""
        result = validate_model("claude-opus-4.6")
        # Should hit hardcoded catalog via dot→dash normalization
        assert result.status != ValidationStatus.NOT_FOUND

    @patch(
        "future_token_predictor.providers.foundry_catalog.check_model_exists",
        side_effect=Exception("offline"),
    )
    def test_fully_offline(self, _mock):
        """Standard model still validates when live catalog is unreachable."""
        result = validate_model("gpt-4o")
        assert result.is_valid
