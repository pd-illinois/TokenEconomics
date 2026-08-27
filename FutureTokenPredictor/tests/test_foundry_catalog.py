"""Tests for foundry_catalog — live Azure AI catalog client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from future_token_predictor.providers.foundry_catalog import (
    check_model_exists,
    fetch_catalog_models,
)


class TestCheckModelExists:
    """Single-model lookup tests."""

    @patch("future_token_predictor.providers.foundry_catalog.httpx.post")
    def test_model_found(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "summaries": [{"name": "claude-opus-4-6", "publisher": "Anthropic"}],
            "totalCount": 1,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        assert check_model_exists("claude-opus-4-6") is True

        # Verify the API was called with the correct name filter
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        filters = body["filters"]
        assert any(
            f["field"] == "name" and "claude-opus-4-6" in f["values"]
            for f in filters
        )

    @patch("future_token_predictor.providers.foundry_catalog.httpx.post")
    def test_model_not_found(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"summaries": [], "totalCount": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        assert check_model_exists("totally-fake-model") is False

    @patch(
        "future_token_predictor.providers.foundry_catalog.httpx.post",
        side_effect=Exception("connection refused"),
    )
    def test_network_error_returns_false(self, _mock):
        assert check_model_exists("gpt-4o") is False


class TestFetchCatalogModels:
    """Full catalog fetch with pagination."""

    @patch("future_token_predictor.providers.foundry_catalog._read_cache", return_value=None)
    @patch("future_token_predictor.providers.foundry_catalog._write_cache")
    @patch("future_token_predictor.providers.foundry_catalog.httpx.post")
    def test_single_page(self, mock_post, mock_write, _mock_read):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "summaries": [
                {"name": "gpt-4o"},
                {"name": "Claude-Opus-4-6"},
            ],
            "totalCount": 2,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        models = fetch_catalog_models()
        assert "gpt-4o" in models
        assert "claude-opus-4-6" in models  # lowercased
        mock_write.assert_called_once()

    @patch("future_token_predictor.providers.foundry_catalog._read_cache")
    def test_cache_hit(self, mock_read):
        mock_read.return_value = {"gpt-4o", "claude-opus-4-6"}
        models = fetch_catalog_models()
        assert models == {"gpt-4o", "claude-opus-4-6"}

    @patch("future_token_predictor.providers.foundry_catalog._read_cache", return_value=None)
    @patch(
        "future_token_predictor.providers.foundry_catalog.httpx.post",
        side_effect=Exception("timeout"),
    )
    def test_api_failure_returns_empty(self, _mock_post, _mock_read):
        models = fetch_catalog_models()
        assert models == set()
